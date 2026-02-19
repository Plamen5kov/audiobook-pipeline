import {
  Controller, Get, Post, Param, Req, Res, UploadedFile,
  UseInterceptors, HttpCode, HttpStatus,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { Request, Response } from 'express';
import { ProxyService } from './proxy.service';

@Controller()
export class ProxyController {
  constructor(private readonly proxy: ProxyService) {}

  // ── Health ────────────────────────────────────────────────────

  @Get('health')
  health() {
    return { status: 'ok' };
  }

  // ── Voices ────────────────────────────────────────────────────

  @Get('voices')
  async listVoices() {
    const { data } = await this.proxy.forwardJson('GET', '/voices');
    return data;
  }

  @Post('voices/upload')
  @UseInterceptors(FileInterceptor('file', { storage: undefined })) // memory storage
  async uploadVoice(@UploadedFile() file: Express.Multer.File) {
    return this.proxy.forwardUpload(file);
  }

  @Get('voices/:filename')
  async getVoice(@Param('filename') filename: string, @Res({ passthrough: true }) res: Response) {
    res.set({ 'Content-Type': 'audio/wav' });
    return this.proxy.streamAudio(`/voices/${filename}`);
  }

  // ── Audio output ──────────────────────────────────────────────

  @Get('audio/:filename')
  async getAudio(@Param('filename') filename: string, @Res({ passthrough: true }) res: Response) {
    res.set({ 'Content-Type': 'audio/wav' });
    return this.proxy.streamAudio(`/audio/${filename}`);
  }

  // ── Status ────────────────────────────────────────────────────

  @Get('status/:jobId')
  async readStatus(@Param('jobId') jobId: string) {
    const { data } = await this.proxy.forwardJson('GET', `/status/${jobId}`);
    return data;
  }

  @Post('status/:jobId')
  @HttpCode(HttpStatus.OK)
  async writeStatus(@Param('jobId') jobId: string, @Req() req: Request) {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', `/status/${jobId}`, body);
    return data;
  }

  // ── Pipeline triggers ─────────────────────────────────────────

  @Post('api/analyze')
  @HttpCode(HttpStatus.OK)
  async analyze(@Req() req: Request) {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/analyze', body);
    return data;
  }

  @Post('api/synthesize')
  @HttpCode(HttpStatus.OK)
  async synthesize(@Req() req: Request) {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/synthesize', body);
    return data;
  }
}

/** Collect the raw request body as a Buffer. */
function readBody(req: Request): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}
