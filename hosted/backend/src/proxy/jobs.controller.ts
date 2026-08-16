import {
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Query,
  Req,
  Res,
} from "@nestjs/common";
import { Request, Response } from "express";
import { ProxyService } from "./proxy.service";
import { PathTraversalPipe } from "../pipes/path-traversal.pipe";
import { readBody } from "../utils/read-body";

/**
 * Reading a run's workspace: which runs exist, how far each got, what each
 * stage produced, and the individual takes.
 *
 * Everything here is a pass-through to studio-api. The gateway's job is to be
 * the only thing the browser talks to, so the ids still go through the path
 * guard even though studio-api validates them again.
 */
@Controller("api/jobs")
export class JobsController {
  constructor(private readonly proxy: ProxyService) {}

  @Get()
  async listJobs(): Promise<unknown> {
    const { data } = await this.proxy.forwardJson("GET", "/api/jobs");
    return data;
  }

  @Get(":jobId")
  async getJob(
    @Param("jobId", PathTraversalPipe) jobId: string,
  ): Promise<unknown> {
    const { data } = await this.proxy.forwardJson("GET", `/api/jobs/${jobId}`);
    return data;
  }

  @Get(":jobId/stages/:stage")
  async getStage(
    @Param("jobId", PathTraversalPipe) jobId: string,
    @Param("stage", PathTraversalPipe) stage: string,
  ): Promise<unknown> {
    const { data } = await this.proxy.forwardJson(
      "GET",
      `/api/jobs/${jobId}/stages/${stage}`,
    );
    return data;
  }

  @Get(":jobId/segments")
  async listSegments(
    @Param("jobId", PathTraversalPipe) jobId: string,
    @Query("failed") failed?: string,
    @Query("speaker") speaker?: string,
  ): Promise<unknown> {
    const query = new URLSearchParams();
    if (failed === "true") query.set("failed", "true");
    if (speaker) query.set("speaker", speaker);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const { data } = await this.proxy.forwardJson(
      "GET",
      `/api/jobs/${jobId}/segments${suffix}`,
    );
    return data;
  }

  @Get(":jobId/segments/:segmentId/audio")
  async getSegmentAudio(
    @Param("jobId", PathTraversalPipe) jobId: string,
    @Param("segmentId") segmentId: string,
    @Req() req: Request,
    @Res() res: Response,
  ): Promise<void> {
    const { stream, status, headers } = await this.proxy.streamAudio(
      `/api/jobs/${jobId}/segments/${Number(segmentId)}/audio`,
      req.headers["range"] as string | undefined,
    );
    res.status(status).set({ "Content-Type": "audio/wav", ...headers });
    stream.pipe(res);
  }

  @Post(":jobId/redo")
  @HttpCode(HttpStatus.OK)
  async redo(
    @Param("jobId", PathTraversalPipe) jobId: string,
    @Req() req: Request,
  ): Promise<unknown> {
    const body = await readBody(req);
    const { data } = await this.proxy.forwardJson(
      "POST",
      `/api/jobs/${jobId}/redo`,
      body,
    );
    return data;
  }
}
