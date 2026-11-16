import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// 只做「有沒有 cookie」的樂觀檢查；真正的身分驗證在後端每個 API
export function proxy(request: NextRequest) {
  if (!request.cookies.has("plantsight_session")) {
    // 登入後回到原本要去的頁面（例如 AI 工具的連線同意頁）；login 頁只接受站內路徑
    const login = new URL("/login", request.url);
    if (request.nextUrl.pathname !== "/") login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|api|_next|favicon.ico).*)"],
};
