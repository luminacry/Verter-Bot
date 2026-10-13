//! Grant a SID modify access to the workspace directory.

use std::path::Path;
use std::ptr;

use anyhow::{Result, bail};
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Authorization::{
    EXPLICIT_ACCESS_W, GRANT_ACCESS, GetNamedSecurityInfoW, SE_FILE_OBJECT, SetEntriesInAclW,
    SetNamedSecurityInfoW, TRUSTEE_IS_SID, TRUSTEE_IS_USER, TRUSTEE_W,
};
use windows_sys::Win32::Security::{
    CONTAINER_INHERIT_ACE, DACL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE, PSID,
};

use crate::util::{FILE_ALL_ACCESS, wide_os};

/// Add an allow ACE for `sid` on `path`.
///
/// # Errors
///
/// Returns an error when the DACL cannot be read or written.
pub fn grant(path: &Path, sid: PSID) -> Result<()> {
    let name = wide_os(path.as_os_str());
    let mut dacl = ptr::null_mut();
    let mut descriptor = ptr::null_mut();
    // SAFETY: `name` is a null-terminated path. The out pointers are live locals.
    let read = unsafe {
        GetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            &raw mut dacl,
            ptr::null_mut(),
            &raw mut descriptor,
        )
    };
    if read != 0 {
        bail!("GetNamedSecurityInfoW failed: Win32 error {read}");
    }
    let access = EXPLICIT_ACCESS_W {
        grfAccessPermissions: FILE_ALL_ACCESS,
        grfAccessMode: GRANT_ACCESS,
        grfInheritance: OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
        Trustee: TRUSTEE_W {
            TrusteeForm: TRUSTEE_IS_SID,
            TrusteeType: TRUSTEE_IS_USER,
            ptstrName: sid.cast(),
            ..TRUSTEE_W::default()
        },
    };
    let mut new_acl = ptr::null_mut();
    // SAFETY: `access` is a live explicit entry and `dacl` came from `GetNamedSecurityInfoW`.
    let wrote = unsafe { SetEntriesInAclW(1, &raw const access, dacl, &raw mut new_acl) };
    if wrote != 0 {
        free_descriptor(descriptor);
        bail!("SetEntriesInAclW failed: Win32 error {wrote}");
    }
    // SAFETY: `new_acl` is the ACL just built and `name` is still valid.
    let applied = unsafe {
        SetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            new_acl,
            ptr::null(),
        )
    };
    // SAFETY: both pointers were allocated by the security APIs above.
    unsafe {
        if !new_acl.is_null() {
            LocalFree(new_acl.cast());
        }
    }
    free_descriptor(descriptor);
    if applied != 0 {
        bail!("SetNamedSecurityInfoW failed: Win32 error {applied}");
    }
    Ok(())
}

fn free_descriptor(descriptor: windows_sys::Win32::Security::PSECURITY_DESCRIPTOR) {
    if !descriptor.is_null() {
        // SAFETY: `descriptor` was allocated by `GetNamedSecurityInfoW`.
        unsafe {
            LocalFree(descriptor.cast());
        }
    }
}
