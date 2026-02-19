import { Injectable, HttpException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { StreamableFile } from '@nestjs/common';
import axios, { AxiosError } from 'axios';
import FormData from 'form-data';
import { Readable } from 'stream';

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

  /** Stream an audio file from DGX back to the client. */
  async streamAudio(path: string): Promise<StreamableFile> {
    try {
      const res = await axios.get(`${this.dgxUrl}${path}`, {
        responseType: 'stream',
        timeout: 0,
      });
      return new StreamableFile(res.data as Readable, {
        type: 'audio/wav',
      });
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
