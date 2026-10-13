//! Job Object limits and process-tree kill.

use std::ptr;
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use windows_sys::Win32::Foundation::{CloseHandle, FALSE, HANDLE, WAIT_TIMEOUT};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_PROCESS_MEMORY, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JobObjectExtendedLimitInformation, SetInformationJobObject, TerminateJobObject,
};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, ResumeThread, TerminateProcess, WaitForSingleObject,
};

use crate::util::{last_error, null};

pub struct Job(pub HANDLE);

impl Drop for Job {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: `self.0` is an open job handle from `CreateJobObjectW`, closed once.
            unsafe {
                CloseHandle(self.0);
            }
            self.0 = ptr::null_mut();
        }
    }
}

/// Create a job that dies with this handle and caps memory and process count.
///
/// # Errors
///
/// Returns an error when the job cannot be created or configured.
pub fn create(memory_bytes: usize, processes: u32) -> Result<Job> {
    // SAFETY: both attribute pointers are null, which creates an unnamed job.
    let handle = unsafe { CreateJobObjectW(null(), null()) };
    if handle.is_null() {
        bail!(last_error("CreateJobObjectW"));
    }
    let job = Job(handle);
    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_JOB_MEMORY;
    info.BasicLimitInformation.ActiveProcessLimit = processes.max(1);
    info.ProcessMemoryLimit = memory_bytes;
    info.JobMemoryLimit = memory_bytes;
    let size = u32::try_from(size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()).unwrap_or(u32::MAX);
    // SAFETY: `info` is a live `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` and `size` is its byte length.
    let ok = unsafe {
        SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            ptr::from_ref(&info).cast(),
            size,
        )
    };
    if ok == FALSE {
        bail!(last_error("SetInformationJobObject"));
    }
    Ok(job)
}

/// Put a suspended process in the job, then start it.
///
/// # Errors
///
/// Returns an error if the process cannot join the job. The process is terminated in that case.
pub fn assign_and_resume(job: &Job, process: HANDLE, thread: HANDLE) -> Result<()> {
    // SAFETY: `job.0` and `process` are open handles for this call.
    let assigned = unsafe { AssignProcessToJobObject(job.0, process) };
    if assigned == FALSE {
        // SAFETY: `process` is the suspended process we just created and must not run unsandboxed.
        unsafe {
            TerminateProcess(process, 1);
        }
        bail!(last_error("AssignProcessToJobObject"));
    }
    // SAFETY: `thread` is the primary thread of that suspended process.
    let resumed = unsafe { ResumeThread(thread) };
    if resumed == u32::MAX {
        bail!(last_error("ResumeThread"));
    }
    Ok(())
}

/// Wait for `process`, killing the job on timeout.
///
/// # Errors
///
/// Returns an error when the exit code cannot be read.
pub fn wait(process: HANDLE, job: &Job, timeout: Duration) -> Result<(Option<u32>, bool)> {
    let millis = u32::try_from(timeout.as_millis()).unwrap_or(u32::MAX);
    // SAFETY: `process` is an open process handle.
    let waited = unsafe { WaitForSingleObject(process, millis) };
    let timed_out = waited == WAIT_TIMEOUT;
    if timed_out {
        terminate(job, process);
    }
    exit_code(process, timed_out)
}

/// Wait in short slices so the caller can drain stdout/stderr pipes.
///
/// # Errors
///
/// Returns an error when the exit code cannot be read.
pub fn wait_polling(
    process: HANDLE,
    job: &Job,
    timeout: Duration,
    mut tick: impl FnMut(),
) -> Result<(Option<u32>, bool)> {
    let started = Instant::now();
    loop {
        tick();
        let remaining = timeout.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            terminate(job, process);
            tick();
            return exit_code(process, true);
        }
        let slice = remaining.min(Duration::from_millis(15));
        let millis = u32::try_from(slice.as_millis()).unwrap_or(15);
        // SAFETY: `process` is an open process handle.
        let waited = unsafe { WaitForSingleObject(process, millis) };
        if waited != WAIT_TIMEOUT {
            tick();
            return exit_code(process, false);
        }
    }
}

fn terminate(job: &Job, process: HANDLE) {
    // SAFETY: `job.0` is the job that owns the process tree.
    unsafe {
        TerminateJobObject(job.0, 1);
        WaitForSingleObject(process, 5_000);
    }
}

fn exit_code(process: HANDLE, timed_out: bool) -> Result<(Option<u32>, bool)> {
    let mut code = 0_u32;
    // SAFETY: `code` is a live `u32` and `process` is still open.
    let ok = unsafe { GetExitCodeProcess(process, &raw mut code) };
    if ok == FALSE {
        bail!(last_error("GetExitCodeProcess"));
    }
    // 259 is `STILL_ACTIVE`.
    if code == 259 {
        Ok((None, timed_out))
    } else {
        Ok((Some(code), timed_out))
    }
}
