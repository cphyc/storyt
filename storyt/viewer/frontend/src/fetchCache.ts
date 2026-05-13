const cache = new Map<string, Promise<unknown>>();

export function cachedFetch(url: string): Promise<unknown> {
  if (!cache.has(url)) {
    cache.set(
      url,
      fetch(url)
        .then((r) => (r.ok ? r.json() : []))
        .catch(() => []),
    );
  }
  return cache.get(url)!;
}
