import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Consent, type Info } from "./Consent";

const BACKEND = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8370";

async function ConnectRequest({ searchParams }: { searchParams: PageProps<"/connect">["searchParams"] }) {
  const req = (await searchParams).req;
  const rid = typeof req === "string" ? req : "";
  const res = await fetch(`${BACKEND}/api/oauth/requests/${encodeURIComponent(rid)}`, {
    headers: { cookie: (await cookies()).toString() }, cache: "no-store",
  });
  if (res.status === 401) redirect(`/login?next=${encodeURIComponent(`/connect?req=${encodeURIComponent(rid)}`)}`);
  const body = (await res.json().catch(() => ({}))) as Info & { detail?: string };
  if (res.ok) return <Consent rid={rid} info={body} />;
  return (
    <div className="mt-6 rounded-md border border-rule bg-sheet p-6">
      <h1 className="text-xl font-medium">無法完成連線</h1>
      <p className="mt-2 text-muted break-words">{body.detail ?? "讀取連線要求失敗"}</p>
    </div>
  );
}

export default function ConnectPage({ searchParams }: PageProps<"/connect">) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center px-4 py-10">
      <p className="text-xl font-semibold tracking-tight text-river">plantsight</p>
      <Suspense fallback={<p className="mt-6 text-muted">讀取連線要求…</p>}>
        <ConnectRequest searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
