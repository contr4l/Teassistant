/**
 * Phone photos are often 3–8 MB. Shrink images before upload: long edge ≤ 2560px, JPEG quality 0.85.
 * These are implementation defaults (docs/ui-guidelines.md §8.6) pending checks on real exam photos.
 * PDFs and small images are sent unchanged. If the browser cannot decode the image (e.g. some HEIC files),
 * the original is returned and the server's format check reports the problem.
 */
const MAX_EDGE = 2560
const QUALITY = 0.85
const SKIP_BELOW_BYTES = 1.2 * 1024 * 1024

export type PreparedFile = { file: File; converted: boolean; originalSize: number }

export async function prepareUpload(file: File): Promise<PreparedFile> {
  const original = { file, converted: false, originalSize: file.size }
  const isHeic = /image\/hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name)
  if (!file.type.startsWith('image/') && !isHeic) return original
  if (!isHeic && file.size < SKIP_BELOW_BYTES && file.type !== 'image/webp') return original
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
    const width = Math.round(bitmap.width * scale)
    const height = Math.round(bitmap.height * scale)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) { bitmap.close(); return original }
    context.fillStyle = '#fff'
    context.fillRect(0, 0, width, height)
    context.drawImage(bitmap, 0, 0, width, height)
    bitmap.close()
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY))
    if (!blob) return original
    // Keep the original when re-encoding does not help (already small PNG/JPEG), unless the format must change.
    if (!isHeic && file.type !== 'image/webp' && blob.size >= file.size) return original
    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
    return { file: new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified }), converted: true, originalSize: file.size }
  } catch {
    return original
  }
}
