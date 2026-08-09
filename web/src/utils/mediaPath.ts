const MEDIA_ROUTE_DIRECTORIES = ["clips", "exports", "recordings"] as const;

/** Convert a backend filesystem path to a path served by the Frigate proxy. */
export function mediaPathToUrlPath(path: string): string {
  const normalized = path.replaceAll("\\", "/");

  for (const directory of MEDIA_ROUTE_DIRECTORIES) {
    const relativePrefix = `${directory}/`;
    if (normalized.startsWith(relativePrefix)) {
      return normalized;
    }

    const absoluteMarker = `/${relativePrefix}`;
    const markerIndex = normalized.indexOf(absoluteMarker);
    if (markerIndex >= 0) {
      return normalized.slice(markerIndex + 1);
    }
  }

  return normalized.replace(/^\/+/, "");
}
