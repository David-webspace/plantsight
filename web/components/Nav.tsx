"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Me } from "@/lib/types";

type Item = { href: string; label: string };

export function navItems(me: Me): Item[] {
  if (me.is_admin) return [{ href: "/admin", label: "稽核" }, { href: "/data", label: "資料品質" }];
  const items: Item[] = [{ href: "/", label: "總覽" }];
  if (me.can_review) items.push({ href: "/data", label: "資料品質" });
  return items;
}

export function Brand() {
  return (
    <div>
      <p className="text-xl font-semibold tracking-tight text-river">plantsight</p>
      <p className="text-xs text-muted">Arcwell 廠務數據</p>
    </div>
  );
}

export function Nav({ me }: { me: Me }) {
  const path = usePathname();
  const items = navItems(me);
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    // 整頁載入而非前端路由：清掉記憶體中的聊天、Router Cache 與 Activity 保留的頁面狀態，否則下一位登入者會看到上一位的對話與資料
    window.location.replace("/login");
  }

  return (
    <>
      <aside className="sticky top-0 hidden h-dvh w-48 shrink-0 flex-col gap-6 border-r border-rule px-4 py-6 md:flex">
      <Brand />
      <nav aria-label="主選單" className="flex flex-1 flex-col gap-1">
        {items.map((it) => (
          <Link key={it.href} href={it.href} aria-current={active(it.href) ? "page" : undefined}
            className={`rounded px-3 py-2 text-base ${active(it.href) ? "bg-river text-white" : "text-ink hover:bg-river-wash"}`}>
            {it.label}
          </Link>
        ))}
        <div className="mt-auto border-t border-rule pt-3 text-sm">
          <p className="font-medium break-words">{me.name}</p>
          <p className="text-xs break-words text-muted">{me.role_label}・{me.scope}</p>
          <button onClick={logout} className="mt-2 text-xs text-river hover:underline">登出</button>
        </div>
      </nav>
      </aside>
      <nav aria-label="主選單" className="fixed inset-x-0 bottom-0 z-20 flex border-t border-rule bg-sheet md:hidden">
        {items.map((it) => (
          <Link key={it.href} href={it.href} aria-current={active(it.href) ? "page" : undefined}
            className={`flex-1 py-3 text-center text-sm ${active(it.href) ? "text-river" : "text-muted"}`}>
            {it.label}
          </Link>
        ))}
        <button onClick={logout} className="px-3 text-xs text-muted">登出</button>
      </nav>
    </>
  );
}
