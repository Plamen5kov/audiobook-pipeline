import { Controller, Get, Param, Req, Res } from '@nestjs/common';
import { Request, Response } from 'express';
import { ProxyService } from './proxy.service';
import { PathTraversalPipe } from '../pipes/path-traversal.pipe';

@Controller('audio')
export class AudioController {
  constructor(private readonly proxy: ProxyService) {}

  @Get(':filename')
  async getAudio(
    @Param('filename', PathTraversalPipe) filename: string,
    @Req() req: Request,
    @Res() res: Response,
  ): Promise<void> {
    const { stream, status, headers } = await this.proxy.streamAudio(
      `/audio/${filename}`,
      req.headers['range'] as string | undefined,
    );
    res.status(status).set({ 'Content-Type': 'audio/wav', ...headers });
    stream.pipe(res);
  }
}
