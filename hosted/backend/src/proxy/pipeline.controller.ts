import { Controller, Post, Req, HttpCode, HttpStatus, UseGuards } from '@nestjs/common';
import { ThrottlerGuard } from '@nestjs/throttler';
import { Request } from 'express';
import { ProxyService } from './proxy.service';
import { readBody } from '../utils/read-body';

@Controller('api')
@UseGuards(ThrottlerGuard)
export class PipelineController {
  constructor(private readonly proxy: ProxyService) {}

  @Post('analyze')
  @HttpCode(HttpStatus.OK)
  async analyze(@Req() req: Request): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/analyze', body);
    return data;
  }

  @Post('synthesize')
  @HttpCode(HttpStatus.OK)
  async synthesize(@Req() req: Request): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson('POST', '/api/synthesize', body);
    return data;
  }

  @Post('re-synthesize')
  @HttpCode(HttpStatus.OK)
  async reSynthesize(@Req() req: Request): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson(
      'POST',
      '/api/re-synthesize',
      body,
    );
    return data;
  }

  @Post('re-stitch')
  @HttpCode(HttpStatus.OK)
  async reStitch(@Req() req: Request): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson(
      'POST',
      '/api/re-stitch',
      body,
    );
    return data;
  }
}
