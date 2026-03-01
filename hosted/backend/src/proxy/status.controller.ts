import {
  Controller,
  Get,
  Post,
  Param,
  Req,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { Request } from 'express';
import { ProxyService } from './proxy.service';
import { readBody } from '../utils/read-body';
import { PathTraversalPipe } from '../pipes/path-traversal.pipe';

@Controller('status')
export class StatusController {
  constructor(private readonly proxy: ProxyService) {}

  @Get(':jobId')
  async readStatus(
    @Param('jobId', PathTraversalPipe) jobId: string,
  ): Promise<unknown> {
    const { data } = await this.proxy.forwardJson('GET', `/status/${jobId}`);
    return data;
  }

  @Post(':jobId')
  @HttpCode(HttpStatus.OK)
  async writeStatus(
    @Param('jobId', PathTraversalPipe) jobId: string,
    @Req() req: Request,
  ): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson(
      'POST',
      `/status/${jobId}`,
      body,
    );
    return data;
  }
}
