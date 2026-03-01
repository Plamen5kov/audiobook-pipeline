import { Module } from '@nestjs/common';
import { HealthController } from './health.controller';
import { VoicesController } from './voices.controller';
import { AudioController } from './audio.controller';
import { StatusController } from './status.controller';
import { PipelineController } from './pipeline.controller';
import { ProxyService } from './proxy.service';

@Module({
  controllers: [
    HealthController,
    VoicesController,
    AudioController,
    StatusController,
    PipelineController,
  ],
  providers: [ProxyService],
})
export class ProxyModule {}
