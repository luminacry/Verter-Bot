//! `AppContainer` profile and process creation.

use std::mem::size_of;
use std::ptr;

use anyhow::{Result, bail};
use windows_sys::Win32::Foundation::{CloseHandle, FALSE, HANDLE, TRUE};
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeriveAppContainerSidFromAppContainerName,
};
use windows_sys::Win32::Security::{
    CreateWellKnownSid, FreeSid, PSID, SECURITY_CAPABILITIES, SECURITY_MAX_SID_SIZE,
    SID_AND_ATTRIBUTES, WinCapabilityInternetClientSid,
};
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessW,
    DeleteProcThreadAttributeList, EXTENDED_STARTUPINFO_PRESENT, InitializeProcThreadAttributeList,
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, PROCESS_INFORMATION, STARTUPINFOEXW, STARTUPINFOW,
    UpdateProcThreadAttribute,
};

use crate::util::{
    SE_GROUP_ENABLED, StartupStdio, command_line, environment_block, last_error, null, null_mut,
    wide,
};

const PROFILE: &str = "verter.sandbox";
const ALREADY_EXISTS: i32 = i32::from_be_bytes(0x8007_00B7u32.to_be_bytes());

pub struct Sid(pub PSID);

impl Drop for Sid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: the SID was allocated by an AppContainer API and is freed once.
            unsafe {
                FreeSid(self.0);
            }
            self.0 = ptr::null_mut();
        }
    }
}

/// Create the profile when needed and return its SID.
///
/// # Errors
///
/// Returns an error when the profile cannot be created or resolved.
pub fn profile_sid() -> Result<Sid> {
    let name = wide(PROFILE);
    let display = wide("Verter sandbox");
    let description = wide("Verter-Bot AppContainer profile");
    let mut sid = ptr::null_mut();
    // SAFETY: the strings are null-terminated and `sid` is a live out pointer.
    let created = unsafe {
        CreateAppContainerProfile(
            name.as_ptr(),
            display.as_ptr(),
            description.as_ptr(),
            null(),
            0,
            &raw mut sid,
        )
    };
    if created == 0 {
        return Ok(Sid(sid));
    }
    if !sid.is_null() {
        // SAFETY: a failed create may still have allocated a SID.
        unsafe {
            FreeSid(sid);
        }
    }
    if created != ALREADY_EXISTS {
        bail!("CreateAppContainerProfile failed: HRESULT {created}");
    }
    let mut existing = ptr::null_mut();
    // SAFETY: `name` is null-terminated and `existing` is a live out pointer.
    let derived =
        unsafe { DeriveAppContainerSidFromAppContainerName(name.as_ptr(), &raw mut existing) };
    if derived != 0 || existing.is_null() {
        bail!("DeriveAppContainerSidFromAppContainerName failed: HRESULT {derived}");
    }
    Ok(Sid(existing))
}

pub struct Spawned {
    pub process: HANDLE,
    pub thread: HANDLE,
}

impl Drop for Spawned {
    fn drop(&mut self) {
        // SAFETY: each handle is either null or an open process/thread handle closed once.
        unsafe {
            if !self.thread.is_null() {
                CloseHandle(self.thread);
            }
            if !self.process.is_null() {
                CloseHandle(self.process);
            }
        }
        self.thread = ptr::null_mut();
        self.process = ptr::null_mut();
    }
}

/// Start `command` suspended inside the `AppContainer`.
///
/// # Errors
///
/// Returns an error when the process cannot be created. Nothing is resumed.
pub fn spawn_suspended(
    command: &[std::ffi::OsString],
    workspace: &std::path::Path,
    network: bool,
    sid: PSID,
    stdio: StartupStdio,
) -> Result<Spawned> {
    let capability = if network {
        Some(internet_client()?)
    } else {
        None
    };
    let mut attributes = capability.as_ref().map(|sid_bytes| SID_AND_ATTRIBUTES {
        Sid: sid_bytes.as_ptr().cast_mut().cast(),
        Attributes: SE_GROUP_ENABLED,
    });
    let mut caps = SECURITY_CAPABILITIES {
        AppContainerSid: sid,
        Capabilities: attributes.as_mut().map_or(null_mut(), ptr::from_mut),
        CapabilityCount: u32::from(network),
        Reserved: 0,
    };
    let mut startup = STARTUPINFOEXW {
        StartupInfo: STARTUPINFOW {
            cb: u32::try_from(size_of::<STARTUPINFOEXW>()).unwrap_or(0),
            ..STARTUPINFOW::default()
        },
        lpAttributeList: null_mut(),
    };
    stdio.apply_to(&mut startup.StartupInfo);
    startup.StartupInfo.cb = u32::try_from(size_of::<STARTUPINFOEXW>()).unwrap_or(0);
    let mut list_bytes = 0_usize;
    // SAFETY: a null list with a live size pointer is the documented size query.
    unsafe {
        InitializeProcThreadAttributeList(null_mut(), 1, 0, &raw mut list_bytes);
    }
    let mut storage = vec![0_u8; list_bytes];
    let list = storage.as_mut_ptr().cast();
    // SAFETY: `storage` is at least the size just returned, and `caps` outlives `CreateProcessW`.
    let initialized = unsafe { InitializeProcThreadAttributeList(list, 1, 0, &raw mut list_bytes) };
    if initialized == FALSE {
        bail!(last_error("InitializeProcThreadAttributeList"));
    }
    startup.lpAttributeList = list;
    // SAFETY: `list` was initialized above. `caps` stays live through `CreateProcessW`.
    let updated = unsafe {
        UpdateProcThreadAttribute(
            list,
            0,
            usize::try_from(PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES).unwrap_or(0),
            ptr::from_mut(&mut caps).cast(),
            size_of::<SECURITY_CAPABILITIES>(),
            null_mut(),
            null(),
        )
    };
    if updated == FALSE {
        // SAFETY: `list` was initialized above.
        unsafe {
            DeleteProcThreadAttributeList(list);
        }
        bail!(last_error("UpdateProcThreadAttribute"));
    }
    let app = crate::util::application_name(command);
    let mut cmdline = command_line(command);
    let env = environment_block(workspace);
    let cwd = wide_os_cwd(workspace);
    let mut info = PROCESS_INFORMATION::default();
    let flags = CREATE_SUSPENDED
        | CREATE_UNICODE_ENVIRONMENT
        | CREATE_NO_WINDOW
        | EXTENDED_STARTUPINFO_PRESENT;
    // SAFETY: `app`, `cmdline`, `env`, `cwd`, `startup`, and `caps` are live for the call.
    // `startup` begins with `STARTUPINFOW`, which is the layout `CreateProcessW` reads when
    // `EXTENDED_STARTUPINFO_PRESENT` is set. `capability` and `attributes` own the SID
    // bytes that `caps` points at.
    let created = unsafe {
        CreateProcessW(
            app.as_ptr(),
            cmdline.as_mut_ptr(),
            null(),
            null(),
            TRUE,
            flags,
            env.as_ptr().cast(),
            cwd.as_ptr(),
            ptr::from_ref(&startup).cast(),
            &raw mut info,
        )
    };
    drop((capability, attributes, env, cwd, app, cmdline));
    // SAFETY: `list` was initialized and must be deleted even when creation fails.
    unsafe {
        DeleteProcThreadAttributeList(list);
    }
    if created == FALSE {
        bail!(last_error("CreateProcessW"));
    }
    Ok(Spawned {
        process: info.hProcess,
        thread: info.hThread,
    })
}

fn internet_client() -> Result<Vec<u8>> {
    let mut sid = vec![0_u8; SECURITY_MAX_SID_SIZE as usize];
    let mut len = u32::try_from(sid.len()).unwrap_or(SECURITY_MAX_SID_SIZE);
    // SAFETY: `sid` has room for a well-known SID and `len` is its capacity.
    let ok = unsafe {
        CreateWellKnownSid(
            WinCapabilityInternetClientSid,
            null_mut(),
            sid.as_mut_ptr().cast(),
            &raw mut len,
        )
    };
    if ok == FALSE {
        bail!(last_error("CreateWellKnownSid"));
    }
    let len = usize::try_from(len).unwrap_or(sid.len());
    sid.truncate(len);
    Ok(sid)
}

fn wide_os_cwd(path: &std::path::Path) -> Vec<u16> {
    crate::util::wide_os(crate::util::native_path(path).as_os_str())
}
