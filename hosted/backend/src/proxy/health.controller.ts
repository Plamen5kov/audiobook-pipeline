import { Controller, Get, Logger } from '@nestjs/common';
import { ProxyService } from './proxy.service';

@Controller()
export class HealthController {
  private readonly logger = new Logger(HealthController.name);

  constructor(private readonly proxy: ProxyService) {}

  @Get('health')
  health(): { status: string } {
    return { status: 'ok' };
  }

  @Get('services/health')
  async servicesHealth(): Promise<unknown> {
    this.logger.log('Checking services health');
    const { data } = await this.proxy.forwardJson('GET', '/services/health');
    return data;
  }
}
