// 稽核紀錄在資料庫存代碼（穩定、好查詢），畫面顯示中文；未列入的代碼原樣顯示
export const EVENT_LABEL: Record<string, string> = {
  chat: "問答", login: "登入", login_failed: "登入失敗", export: "匯出 Excel",
  review_approve: "核准資料修正", review_reject: "駁回資料修正",
};

export function label(map: Record<string, string>, code: string | null): string {
  return code ? map[code] ?? code : "";
}
