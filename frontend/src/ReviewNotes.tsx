import { useEffect, useState } from "react";

type ReviewData = {
  date: string;
  note: string;
  tags: string[];
  updated_at: string | null;
};

type ReviewState =
  | { kind: "empty" }
  | { kind: "loading" }
  | { kind: "ready" | "saving" | "saved"; data: ReviewData }
  | { kind: "error"; data?: ReviewData };

export function ReviewNotes({ reviewDate }: { reviewDate: string | null }) {
  const [state, setState] = useState<ReviewState>({ kind: "empty" });
  const [tagInput, setTagInput] = useState("");

  useEffect(() => {
    if (!reviewDate) {
      setState({ kind: "empty" });
      return;
    }
    const controller = new AbortController();
    setState({ kind: "loading" });
    void fetch(`/api/reviews/${reviewDate}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`复盘接口返回 ${response.status}`);
        }
        return response.json() as Promise<ReviewData>;
      })
      .then((data) => setState({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setState({ kind: "error" });
      });
    return () => controller.abort();
  }, [reviewDate]);

  const data = "data" in state ? state.data : undefined;

  function updateData(update: (current: ReviewData) => ReviewData) {
    if (data) {
      setState({ kind: "ready", data: update(data) });
    }
  }

  function addTag() {
    const tag = tagInput.trim();
    if (!tag || !data || data.tags.includes(tag)) {
      return;
    }
    updateData((current) => ({ ...current, tags: [...current.tags, tag] }));
    setTagInput("");
  }

  async function save() {
    if (!reviewDate || !data) {
      return;
    }
    setState({ kind: "saving", data });
    try {
      const response = await fetch(`/api/reviews/${reviewDate}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: data.note, tags: data.tags }),
      });
      if (!response.ok) {
        throw new Error(`复盘保存返回 ${response.status}`);
      }
      const saved = (await response.json()) as ReviewData;
      setState({ kind: "saved", data: saved });
    } catch {
      setState({ kind: "error", data });
    }
  }

  return (
    <section className="review-notes" aria-labelledby="review-notes-title">
      <header className="review-notes__header">
        <div>
          <p className="section-kicker">人工记录</p>
          <h2 id="review-notes-title">每日复盘笔记</h2>
        </div>
        <div className="review-date">{reviewDate ?? "尚无实际数据日期"}</div>
      </header>

      {state.kind === "empty" && (
        <div className="review-empty">运行每日任务后，可为实际数据日期记录复盘内容。</div>
      )}
      {state.kind === "loading" && <div className="review-empty">正在读取复盘记录…</div>}
      {state.kind === "error" && !data && (
        <div className="review-empty review-error">读取复盘记录失败。</div>
      )}
      {data && (
        <div className="review-editor">
          <label>
            <span>复盘笔记</span>
            <textarea
              value={data.note}
              placeholder="记录当天市场结构、策略表现和后续观察…"
              onChange={(event) =>
                updateData((current) => ({ ...current, note: event.target.value }))
              }
            />
          </label>
          <div className="tag-editor">
            <div className="tag-input-row">
              <label>
                <span>新标签</span>
                <input
                  value={tagInput}
                  onChange={(event) => setTagInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      addTag();
                    }
                  }}
                />
              </label>
              <button type="button" onClick={addTag}>添加标签</button>
            </div>
            <div className="tag-list" aria-label="自定义标签">
              {data.tags.length === 0 && <span className="no-tags">尚无标签</span>}
              {data.tags.map((tag) => (
                <span className="review-tag" key={tag}>
                  {tag}
                  <button
                    type="button"
                    aria-label={`移除 ${tag}`}
                    onClick={() =>
                      updateData((current) => ({
                        ...current,
                        tags: current.tags.filter((value) => value !== tag),
                      }))
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
          <div className="review-actions">
            <span data-testid="review-save-status">
              {state.kind === "saving"
                ? "保存中"
                : state.kind === "saved"
                  ? "保存成功"
                  : state.kind === "error"
                    ? "保存失败"
                    : data.updated_at
                      ? "已保存"
                      : "尚无笔记"}
            </span>
            <button type="button" disabled={state.kind === "saving"} onClick={() => void save()}>
              保存复盘
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
