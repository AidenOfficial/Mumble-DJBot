// 分片上传客户端(配合 web_upload.py)。每片 32MB 以内,单片失败自动重试,
// 服务器返回 409 时按它记录的 received 续传,所以断网/切后台后重试也不会重传整份文件。

const BASE = '..'

export interface UploadResult {
  status: 'done' | 'error'
  path?: string
  item_id?: string
  title?: string
  extracted?: boolean
  final_size?: number
  error?: string
}

export interface UploadProgress {
  phase: 'uploading' | 'processing'
  sent: number
  total: number
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export class UploadError extends Error {
  code: string
  detail?: unknown
  constructor(code: string, detail?: unknown) {
    super(code)
    this.code = code
    this.detail = detail
  }
}

export async function uploadFile(
  file: File,
  onProgress: (p: UploadProgress) => void,
  opts: { targetdir?: string; signal?: AbortSignal } = {},
): Promise<UploadResult> {
  const init = await fetch(`${BASE}/api/upload/init`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name, size: file.size, targetdir: opts.targetdir ?? 'uploads/' }),
    signal: opts.signal,
  })
  if (!init.ok) {
    const body = await init.json().catch(() => ({}))
    throw new UploadError(body.error ?? `http_${init.status}`, body)
  }
  const { upload_id: id, chunk_size: chunkSize } = await init.json()

  let offset = 0
  let failures = 0
  while (offset < file.size) {
    if (opts.signal?.aborted) throw new UploadError('aborted')
    const chunk = file.slice(offset, Math.min(offset + chunkSize, file.size))
    try {
      const rv = await fetch(`${BASE}/api/upload/${id}?offset=${offset}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: chunk,
        signal: opts.signal,
      })
      if (rv.status === 409) {
        const body = await rv.json().catch(() => ({}))
        if (typeof body.received !== 'number') throw new UploadError('conflict')
        offset = body.received // 按服务器进度续传
        continue
      }
      if (!rv.ok) throw new Error(String(rv.status))
      offset = (await rv.json()).received
      failures = 0
      onProgress({ phase: 'uploading', sent: offset, total: file.size })
    } catch (e) {
      if (opts.signal?.aborted || e instanceof UploadError) throw e
      if (++failures > 5) throw new UploadError('network')
      await sleep(1000 * 2 ** failures) // 2s, 4s, 8s...
    }
  }

  onProgress({ phase: 'processing', sent: file.size, total: file.size })
  const fin = await fetch(`${BASE}/api/upload/${id}/finish`, { method: 'POST', signal: opts.signal })
  if (!fin.ok && fin.status !== 415) throw new UploadError(`http_${fin.status}`)
  // 后台处理(视频抽音轨)可能要一会儿,轮询结果
  for (;;) {
    const st = await (await fetch(`${BASE}/api/upload/${id}`, { signal: opts.signal })).json()
    if (st.status === 'done' || st.status === 'error') return st
    await sleep(1000)
  }
}
