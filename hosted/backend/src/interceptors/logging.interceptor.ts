import {
  Injectable,
  NestInterceptor,
  ExecutionContext,
  CallHandler,
  Logger,
} from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { Request, Response } from 'express';

@Injectable()
export class LoggingInterceptor implements NestInterceptor {
  private readonly logger = new Logger('HTTP');

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const req = context.switchToHttp().getRequest<Request>();
    const { method, url } = req;
    const start = Date.now();

    return next.handle().pipe(
      tap({
        next: () => {
          const res = context.switchToHttp().getResponse<Response>();
          this.logger.log(`${method} ${url} ${res.statusCode} ${Date.now() - start}ms`);
        },
        error: (err: unknown) => {
          const duration = Date.now() - start;
          const message = err instanceof Error ? err.message : String(err);
          this.logger.warn(`${method} ${url} ERR ${duration}ms - ${message}`);
        },
      }),
    );
  }
}
