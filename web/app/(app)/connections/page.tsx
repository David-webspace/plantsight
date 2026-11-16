import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { Grants, type Grant } from "./Grants";

async function Connections() {
  const { grants, mcp_url } = await apiGet<{ grants: Grant[]; mcp_url: string }>("/api/oauth/grants");
  return (
    <>
      <section className="mt-6 rounded-md border border-rule bg-sheet p-5">
        <h2 className="text-lg font-medium">怎麼連接</h2>
        <p className="mt-1 text-sm text-muted break-words">
          在支援 MCP 的 AI 工具（Claude Code、MCP Inspector 等）加入下面的網址。工具會開啟瀏覽器請你登入並同意，不需要複製任何權杖。
        </p>
        <p className="mt-3 text-sm text-muted">MCP 網址</p>
        <code className="num mt-1 block rounded bg-paper px-3 py-2 text-sm break-all">{mcp_url}</code>
        <p className="mt-3 text-sm text-muted">Claude Code</p>
        <code className="num mt-1 block rounded bg-paper px-3 py-2 text-sm break-all">claude mcp add --transport http plantsight {mcp_url}</code>
      </section>
      <Grants grants={grants} />
    </>
  );
}

export default function ConnectionsPage() {
  return (
    <>
      <h1 className="text-2xl font-semibold">連接 AI 工具</h1>
      <p className="mt-1 text-sm text-muted break-words">
        外部 AI 工具以你的身分唯讀查詢生產數據，看得到的範圍和你在這裡看到的相同；每次查詢都記在稽核紀錄。
      </p>
      <Suspense fallback={<p className="mt-6 text-muted">讀取中…</p>}>
        <Connections />
      </Suspense>
    </>
  );
}
