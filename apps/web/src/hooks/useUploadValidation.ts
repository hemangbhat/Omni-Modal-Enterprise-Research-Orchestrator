/**
 * Client-side upload file validation hook.
 *
 * Requirements: 10.5
 *
 * Validates file extension and size before dispatching an upload request.
 * This is a client-side pre-check only; the backend performs authoritative
 * validation (size, MIME type sniffing) as well.
 */

export const ALLOWED_EXTENSIONS = new Set([
  ".pdf",
  ".mp3",
  ".wav",
  ".m4a",
  ".flac",
  ".ogg",
  ".webm",
]);

/** Maximum file size: 50 MiB (matches backend upload_safety.py MAX_FILE_BYTES). */
export const MAX_FILE_BYTES = 52_428_800; // 50 MiB

/**
 * Validate a File object before upload.
 *
 * @returns null if the file is valid; a human-readable error string if it is not.
 */
export function validateUploadFile(file: File): string | null {
  const nameParts = file.name.split(".");
  const ext =
    nameParts.length > 1 ? "." + nameParts.pop()!.toLowerCase() : "";

  if (!ALLOWED_EXTENSIONS.has(ext)) {
    return `File type '${ext || "(no extension)"}' is not supported. Allowed types: PDF, MP3, WAV, M4A, FLAC, OGG, WEBM.`;
  }

  if (file.size > MAX_FILE_BYTES) {
    const sizeMiB = (file.size / (1024 * 1024)).toFixed(1);
    return `File size ${sizeMiB} MiB exceeds the 50 MiB limit.`;
  }

  return null;
}
