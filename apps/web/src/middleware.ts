/**
 * Next.js 16 Edge Middleware.
 *
 * NOTE ON AUTH:
 * Page navigation in a browser never carries an `Authorization: Bearer`
 * header (that only exists on fetch/XHR calls), so this middleware cannot use
 * a bearer token to gate page routes — doing so would make every page
 * unreachable. The authoritative auth boundary is the Python backend, which
 * verifies the JWT on every `/query` and `/ingest/*` request. The frontend
 * attaches the dev token (NEXT_PUBLIC_API_TOKEN) to those API calls via
 * `apiRequest`.
 *
 * This middleware therefore allows page navigation through. If/when a real
 * session-cookie mechanism is added (e.g. NextAuth / Iron Session), reinstate
 * route protection here by reading the signed session cookie.
 */
import { NextResponse } from "next/server";

export function middleware(): NextResponse {
  return NextResponse.next();
}

export const config = {
  // No matcher routes are gated at the edge; the backend enforces auth.
  matcher: [],
};
