use crate::{
    bundle::{Bundle, TrustedKeys, MAX_BUNDLE},
    Error, ErrorKind, Result,
};
use fs2::FileExt;
use std::{
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

fn io_error() -> Error {
    Error::new(
        ErrorKind::ConfigUnavailable,
        "cache",
        "signed cache could not be read or persisted",
    )
}
async fn regular_file(path: &Path, create: bool) -> std::io::Result<tokio::fs::File> {
    let mut options = tokio::fs::OpenOptions::new();
    options
        .read(true)
        .write(create)
        .create(create)
        .truncate(false);
    #[cfg(unix)]
    options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    let file = options.open(path).await?;
    if !file.metadata().await?.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "cache paths must be regular files",
        ));
    }
    Ok(file)
}
async fn read(path: &Path, keys: &TrustedKeys) -> Result<Option<Bundle>> {
    let file = match regular_file(path, false).await {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(io_error()),
    };
    let mut bytes = Vec::new();
    file.take((MAX_BUNDLE + 1) as u64)
        .read_to_end(&mut bytes)
        .await
        .map_err(|_| io_error())?;
    Bundle::verify(&bytes, keys).map(Some)
}
pub(crate) async fn accept(path: &Path, candidate: &Bundle, keys: &TrustedKeys) -> Result<Bundle> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    tokio::fs::create_dir_all(parent)
        .await
        .map_err(|_| io_error())?;
    let mut lock_name = path.as_os_str().to_owned();
    lock_name.push(".lock");
    let lock = regular_file(&PathBuf::from(lock_name), true)
        .await
        .map_err(|_| io_error())?
        .into_std()
        .await;
    // Only nonblocking lock syscalls run on the executor. The outer operation deadline bounds this loop.
    loop {
        match lock.try_lock_exclusive() {
            Ok(()) => break,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                tokio::time::sleep(Duration::from_millis(10)).await
            }
            Err(_) => return Err(io_error()),
        }
    }
    let existing = read(path, keys).await?;
    let selected = if let Some(existing) = existing {
        existing.advance(candidate)?;
        candidate.clone()
    } else {
        candidate.clone()
    };
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    let name = format!(
        ".anchorpass-{}-{}-{}.tmp",
        std::process::id(),
        crate::bundle::now()?,
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    let temp = parent.join(name);
    let result = async {
        let mut file = tokio::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .await
            .map_err(|_| io_error())?;
        file.write_all(&selected.envelope)
            .await
            .map_err(|_| io_error())?;
        file.sync_all().await.map_err(|_| io_error())?;
        drop(file);
        tokio::fs::rename(&temp, path)
            .await
            .map_err(|_| io_error())?;
        #[cfg(unix)]
        {
            let directory = tokio::fs::OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_DIRECTORY)
                .open(parent)
                .await
                .map_err(|_| io_error())?;
            directory.sync_all().await.map_err(|_| io_error())?;
        }
        Ok(())
    }
    .await;
    if result.is_err() {
        let _ = tokio::fs::remove_file(&temp).await;
    }
    drop(lock);
    result?;
    Ok(selected)
}
pub(crate) async fn load(path: &Path, keys: &TrustedKeys) -> Result<Option<Bundle>> {
    read(path, keys).await
}
