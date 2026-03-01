import {
  Controller,
  Get,
  Post,
  Delete,
  Param,
  Req,
  Res,
  UploadedFile,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { Request, Response } from 'express';
import { ProxyService } from './proxy.service';
import { PathTraversalPipe } from '../pipes/path-traversal.pipe';

@Controller('voices')
export class VoicesController {
  constructor(private readonly proxy: ProxyService) {}

  @Get(':engine')
  async listVoices(
    @Param('engine', PathTraversalPipe) engine: string,
  ): Promise<unknown> {
    const { data } = await this.proxy.forwardJson('GET', `/voices/${engine}`);
    return data;
  }

  @Post('upload/:engine')
  @UseInterceptors(
    FileInterceptor('file', {
      storage: undefined,
      limits: { fileSize: 50 * 1024 * 1024 },
    }),
  )
  async uploadVoice(
    @Param('engine', PathTraversalPipe) engine: string,
    @UploadedFile() file: Express.Multer.File,
  ): Promise<unknown> {
    return this.proxy.forwardUpload(file, engine);
  }

  @Get(':engine/:filename')
  async getVoice(
    @Param('engine', PathTraversalPipe) engine: string,
    @Param('filename', PathTraversalPipe) filename: string,
    @Req() req: Request,
    @Res() res: Response,
  ): Promise<void> {
    const { stream, status, headers } = await this.proxy.streamAudio(
      `/voices/${engine}/${filename}`,
      req.headers['range'] as string | undefined,
    );
    res.status(status).set({ 'Content-Type': 'audio/wav', ...headers });
    stream.pipe(res);
  }

  @Delete(':engine/:filename')
  async deleteVoice(
    @Param('engine', PathTraversalPipe) engine: string,
    @Param('filename', PathTraversalPipe) filename: string,
  ): Promise<unknown> {
    const { data } = await this.proxy.forwardJson(
      'DELETE',
      `/voices/${engine}/${filename}`,
    );
    return data;
  }
}
