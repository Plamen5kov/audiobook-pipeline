import { PipeTransform, Injectable, BadRequestException } from '@nestjs/common';

@Injectable()
export class PathTraversalPipe implements PipeTransform<string, string> {
  transform(value: string): string {
    if (value.includes('/') || value.includes('..')) {
      throw new BadRequestException(
        'Parameter must not contain "/" or ".."',
      );
    }
    return value;
  }
}
