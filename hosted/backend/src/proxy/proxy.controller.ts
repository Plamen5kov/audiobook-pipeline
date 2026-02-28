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

  @Get('voices/:engine')
  async listVoices(@Param('engine') engine: string) {
    const { data } = await this.proxy.forwardJson('GET', `/voices/${engine}`);
    return data;
  }

  @Post('voices/upload/:engine')
  @UseInterceptors(FileInterceptor('file', { storage: undefined })) // memory storage
  async uploadVoice(@Param('engine') engine: string, @UploadedFile() file: Express.Multer.File) {
    return this.proxy.forwardUpload(file, engine);
  }

  @Get('voices/:engine/:filename')
  async getVoice(@Param('engine') engine: string, @Param('filename') filename: string, @Req() req: Request, @Res() res: Response): Promise<void> {
    const { stream, status, headers } = await this.proxy.streamAudio(`/voices/${engine}/${filename}`, req.headers['range'] as string | undefined);
    res.status(status).set({ 'Content-Type': 'audio/wav', ...headers });
    stream.pipe(res);
  }

  // ── Audio output ──────────────────────────────────────────────

  @Get('audio/:filename')
  async getAudio(@Param('filename') filename: string, @Req() req: Request, @Res() res: Response): Promise<void> {
    const { stream, status, headers } = await this.proxy.streamAudio(`/audio/${filename}`, req.headers['range'] as string | undefined);
    res.status(status).set({ 'Content-Type': 'audio/wav', ...headers });
    stream.pipe(res);
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

  // ── Services health ───────────────────────────────────────────

  @Get('services/health')
  async servicesHealth() {
    const { data } = await this.proxy.forwardJson('GET', '/services/health');
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

  // ── Post-production ─────────────────────────────────────────

  @Post('api/re-synthesize')
  @HttpCode(HttpStatus.OK)
  async reSynthesize(@Req() req: Request) {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/re-synthesize', body);
    return data;
  }

  @Post('api/re-stitch')
  @HttpCode(HttpStatus.OK)
  async reStitch(@Req() req: Request) {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/re-stitch', body);
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
