/** The current URL minus one query param, for history.replaceState. Strips only what the
 * caller consumed, so a sibling param (?auth= next to ?build=) survives for its own reader. */
export function withoutParam(
  url: { pathname: string; search: string; hash: string },
  key: string
): string {
  const params = new URLSearchParams(url.search);
  params.delete(key);
  const qs = params.toString();
  return url.pathname + (qs ? `?${qs}` : "") + url.hash;
}
