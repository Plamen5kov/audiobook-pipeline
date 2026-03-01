import { Injectable, HttpException, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import axios, { AxiosError } from 'axios';
import FormData from 'form-data';
import { Readable } from 'stream';

export interface AudioStream {
  stream: Readable;
  status: number;
  headers: Record<string, string>;
}

@Injectable()
export class ProxyService {
  private readonly logger = new Logger(ProxyService.name);
  private readonly dgxUrl: string;
  private readonly timeout: number;

  constructor(private config: ConfigService) {
    const raw = config.get<string>('DGX_URL');
    if (!raw) {
      throw new Error(
        'DGX_URL environment variable is required. Set it to the DGX file-server URL (e.g. http://host:8080).',
      );
    }
    try {
      new URL(raw);
    } catch {
      throw new Error(
        `DGX_URL is not a valid URL: "${raw}". Expected format: http://host:port`,
      );
    }
    this.dgxUrl = raw.replace(/\/$/, '');

    const timeoutStr = config.get<string>('DGX_TIMEOUT_MS');
    this.timeout = timeoutStr ? parseInt(timeoutStr, 10) : 0;
    this.logger.log(`Proxying to ${this.dgxUrl} (timeout: ${this.timeout || 'none'})`);
  }

  /** Forward a JSON request to the DGX and return the parsed response body. */
  async forwardJson(
    method: 'GET' | 'POST' | 'DELETE',
    path: string,
    body?: Buffer,
  ): Promise<{ data: unknown; status: number }> {
    try {
      const res = await axios.request({
        method,
        url: `${this.dgxUrl}${path}`,
        data: body,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        timeout: this.timeout,
      });
      return { data: res.data, status: res.status };
    } catch (err) {
      this.rethrow(err);
    }
  }

  /** Stream an audio file from DGX, forwarding Range and propagating Content-Length/Content-Range. */
  async streamAudio(
    path: string,
    rangeHeader?: string,
  ): Promise<AudioStream> {
    try {
      const reqHeaders: Record<string, string> = {};
      if (rangeHeader) reqHeaders['Range'] = rangeHeader;

      const res = await axios.get(`${this.dgxUrl}${path}`, {
        responseType: 'stream',
        timeout: this.timeout,
        headers: reqHeaders,
        validateStatus: (s) => s >= 200 && s < 400,
      });

      const headers: Record<string, string> = {};
      for (const h of ['content-length', 'content-range', 'accept-ranges']) {
        const v = res.headers[h];
        if (v) headers[h] = String(v);
      }

      const stream = res.data as Readable;

      stream.on('error', (err) => {
        this.logger.error(`Stream error on ${path}: ${err.message}`);
      });

      return { stream, status: res.status, headers };
    } catch (err) {
      this.rethrow(err);
    }
  }

  /** Forward a multipart file upload to DGX. */
  async forwardUpload(
    file: Express.Multer.File,
    engine: string,
  ): Promise<unknown> {
    const form = new FormData();
    form.append('file', file.buffer, {
      filename: file.originalname,
      contentType: file.mimetype,
    });

    try {
      const res = await axios.post(
        `${this.dgxUrl}/voices/upload/${engine}`,
        form,
        {
          headers: form.getHeaders(),
          timeout: this.timeout,
        },
      );
      return res.data;
    } catch (err) {
      this.rethrow(err);
    }
  }

  private rethrow(err: unknown): never {
    if (err instanceof AxiosError && err.response) {
      const upstream = err.response.data;
      let message: string;

      if (typeof upstream === 'object' && upstream !== null && 'detail' in upstream) {
        message = String((upstream as Record<string, unknown>).detail);
      } else if (typeof upstream === 'string') {
        // Strip HTML tags if the Python server returned an HTML error page
        message = upstream.replace(/<[^>]*>/g, '').trim() || err.message;
      } else {
        message = err.message;
      }

      this.logger.warn(
        `Upstream ${err.config?.method?.toUpperCase()} ${err.config?.url} -> ${err.response.status}: ${message}`,
      );
      throw new HttpException(message, err.response.status);
    }

    const msg = err instanceof Error ? err.message : String(err);
    this.logger.error(`DGX unreachable: ${msg}`);
    throw new HttpException('DGX unreachable', 502);
  }
}
