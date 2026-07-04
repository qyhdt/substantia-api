// 客户端多品牌：按域名切换（yayaok → Yaya，其余 → Substantia）。一套代码，运行时读 hostname。
function resolve(): { name: string; key: string; apiHost: string } {
  const h = (typeof window !== "undefined" ? window.location.hostname : "").toLowerCase();
  if (h.includes("yayaok") || h.includes("yaya"))
    return { name: "Yaya", key: "yaya", apiHost: "api.yayaok.com" };
  return { name: "Substantia", key: "substantia", apiHost: "api.substantia.ai" };
}

export const BRAND = resolve();

// 标签标题按品牌（index.html 里是静态 "Substantia ..."，这里客户端加载时改）
if (typeof document !== "undefined" && BRAND.key !== "substantia") {
  document.title = document.title.replace(/Substantia/g, BRAND.name);
}
