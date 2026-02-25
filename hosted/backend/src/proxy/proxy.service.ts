import { Injectable, HttpException } from '@nestjs/common';
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
  private readonly dgxUrl: string;

  constructor(private config: ConfigService) {
    this.dgxUrl = (config.get<string>('DGX_URL') ?? 'http://localhost:8080').replace(/\/$/, '');
  }

  /** Forward a JSON request to the DGX and return the parsed response body. */
  async forwardJson(method: 'GET' | 'POST', path: string, body?: Buffer): Promise<{ data: unknown; status: number }> {
    try {
      const res = await axios.request({
        method,
        url: `${this.dgxUrl}${path}`,
        data: body,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        timeout: 0, // no timeout — LLM calls can take many minutes
      });
      return { data: res.data, status: res.status };
    } catch (err) {
      this.rethrow(err);
    }
  }

  /** Stream an audio file from DGX, forwarding Range and propagating Content-Length/Content-Range. */
  async streamAudio(path: string, rangeHeader?: string): Promise<AudioStream> {
    try {
      const reqHeaders: Record<string, string> = {};
      if (rangeHeader) reqHeaders['Range'] = rangeHeader;

      const res = await axios.get(`${this.dgxUrl}${path}`, {
        responseType: 'stream',
        timeout: 0,
        headers: reqHeaders,
        validateStatus: (s) => s >= 200 && s < 400,
      });

      const headers: Record<string, string> = {};
      for (const h of ['content-length', 'content-range', 'accept-ranges']) {
        const v = res.headers[h];
        if (v) headers[h] = String(v);
      }

      return { stream: res.data as Readable, status: res.status, headers };
    } catch (err) {
      this.rethrow(err);
    }
  }

  /** Forward a multipart file upload to DGX. */
  async forwardUpload(file: Express.Multer.File): Promise<unknown> {
    const form = new FormData();
    form.append('file', file.buffer, { filename: file.originalname, contentType: file.mimetype });

    try {
      const res = await axios.post(`${this.dgxUrl}/voices/upload`, form, {
        headers: form.getHeaders(),
        timeout: 0,
      });
      return res.data;
    } catch (err) {
      this.rethrow(err);
    }
  }

  private rethrow(err: unknown): never {
    if (err instanceof AxiosError && err.response) {
      throw new HttpException(err.response.data ?? err.message, err.response.status);
    }
    throw new HttpException('DGX unreachable', 502);
  }
}
