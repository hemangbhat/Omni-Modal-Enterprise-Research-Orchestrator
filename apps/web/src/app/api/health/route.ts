import { NextResponse } from "next/server";
import { getServerEnv } from "@/lib/env";
import { getPhaseOneComponents } from "@/lib/phase";

export const runtime = "nodejs";

export function GET() {
  const env = getServerEnv();

  return NextResponse.json({
    service: "web",
    phase: 1,
    status: "ok",
    backendBaseUrl: env.backendBaseUrl,
    components: getPhaseOneComponents()
  });
}
