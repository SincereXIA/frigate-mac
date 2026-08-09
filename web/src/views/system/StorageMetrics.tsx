import { CombinedStorageGraph } from "@/components/graph/CombinedStorageGraph";
import { StorageGraph } from "@/components/graph/StorageGraph";
import { FrigateStats, StorageStats } from "@/types/stats";
import { useEffect, useMemo } from "react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import useSWR from "swr";
import { CiCircleAlert } from "react-icons/ci";
import { FrigateConfig } from "@/types/frigateConfig";
import {
  useFormattedTimestamp,
  useTimeFormat,
  useTimezone,
} from "@/hooks/use-date-utils";
import { RecordingsSummary } from "@/types/review";
import { useTranslation } from "react-i18next";
import { TZDate } from "react-day-picker";
import { Link } from "react-router-dom";
import { useDocDomain } from "@/hooks/use-doc-domain";
import { LuExternalLink } from "react-icons/lu";
import { FaExclamationTriangle } from "react-icons/fa";
import ActivityIndicator from "@/components/indicators/activity-indicator";

type CameraStorage = {
  [key: string]: {
    bandwidth: number;
    usage: number;
    usage_percent: number;
  };
};

type StorageMetricsProps = {
  setLastUpdated: (last: number) => void;
};

type StorageEntry = {
  path: string;
  stats: StorageStats;
};

function findStorageEntry(
  storage: Record<string, StorageStats>,
  canonicalPath: string,
): StorageEntry | undefined {
  const exact = storage[canonicalPath];
  if (exact?.used != undefined && exact.total != undefined) {
    return { path: canonicalPath, stats: exact };
  }

  const directory = canonicalPath.split("/").at(-1);
  if (!directory) {
    return undefined;
  }

  const entry = Object.entries(storage).find(
    ([path, stats]) =>
      path.endsWith(`/${directory}`) &&
      stats.used != undefined &&
      stats.total != undefined,
  );

  return entry ? { path: entry[0], stats: entry[1] } : undefined;
}

export default function StorageMetrics({
  setLastUpdated,
}: StorageMetricsProps) {
  const { data: cameraStorage } = useSWR<CameraStorage>("recordings/storage");
  const { data: stats } = useSWR<FrigateStats>("stats");
  const { data: config } = useSWR<FrigateConfig>("config", {
    revalidateOnFocus: false,
  });
  const { t } = useTranslation(["views/system"]);
  const timezone = useTimezone(config);
  const { getLocaleDocUrl } = useDocDomain();

  const storageEntries = useMemo(() => {
    if (!stats) {
      return undefined;
    }

    return {
      recordings: findStorageEntry(
        stats.service.storage,
        "/media/frigate/recordings",
      ),
      cache: findStorageEntry(stats.service.storage, "/tmp/cache"),
      sharedMemory: findStorageEntry(stats.service.storage, "/dev/shm"),
    };
  }, [stats]);

  const totalStorage = useMemo(() => {
    if (!cameraStorage || !storageEntries?.recordings) {
      return undefined;
    }

    const totalStorage = {
      used: storageEntries.recordings.stats.used,
      camera: 0,
      total: storageEntries.recordings.stats.total,
    };

    Object.values(cameraStorage).forEach(
      (cam) => (totalStorage.camera += cam.usage),
    );
    return totalStorage;
  }, [cameraStorage, storageEntries]);

  useEffect(() => {
    if (totalStorage) {
      setLastUpdated(Math.floor(Date.now() / 1000));
    }
  }, [totalStorage, setLastUpdated]);

  // recordings summary

  const { data: recordingsSummary } = useSWR<RecordingsSummary>([
    "recordings/summary",
    {
      timezone: timezone,
    },
  ]);

  const earliestDate = useMemo(() => {
    const keys = Object.keys(recordingsSummary || {});
    return keys.length
      ? new TZDate(keys[0] + "T00:00:00", timezone).getTime() / 1000
      : null;
  }, [recordingsSummary, timezone]);

  const timeFormat = useTimeFormat(config);
  const format = useMemo(() => {
    return t(`time.formattedTimestampMonthDayYear.${timeFormat}`, {
      ns: "common",
    });
  }, [t, timeFormat]);

  const formattedEarliestDate = useFormattedTimestamp(
    earliestDate || 0,
    format,
    timezone,
  );

  const shmFrameLifetime = useMemo(() => {
    if (!stats || !config) {
      return undefined;
    }

    const shmFrameCount = storageEntries?.sharedMemory?.stats.shm_frame_count;

    if (!shmFrameCount || shmFrameCount <= 0) {
      return undefined;
    }

    let maxCameraFps = 0;

    for (const [name, camStats] of Object.entries(stats.cameras)) {
      if (config.cameras[name]?.enabled && camStats.camera_fps > 0) {
        maxCameraFps = Math.max(maxCameraFps, camStats.camera_fps);
      }
    }

    if (maxCameraFps === 0) {
      return undefined;
    }

    return {
      frames: shmFrameCount,
      lifetime: Math.round((shmFrameCount / maxCameraFps) * 10) / 10,
    };
  }, [stats, config, storageEntries]);

  const sharedMemoryStats = storageEntries?.sharedMemory?.stats;
  const isPosixSharedMemory =
    sharedMemoryStats?.capacity_type === "managed_budget" ||
    sharedMemoryStats?.mount_type === "posix_shared_memory";
  const hasSharedMemoryWarning = isPosixSharedMemory
    ? (sharedMemoryStats?.shm_frame_count ?? 0) < 20
    : (sharedMemoryStats?.total ?? 0) < (sharedMemoryStats?.min_shm ?? 0);

  if (
    !cameraStorage ||
    !stats ||
    !totalStorage ||
    !config ||
    !storageEntries?.cache ||
    !storageEntries.sharedMemory
  ) {
    return (
      <div className="flex size-full items-center justify-center">
        <ActivityIndicator />
      </div>
    );
  }

  return (
    <div className="scrollbar-container mt-4 flex size-full flex-col overflow-y-auto">
      <div className="text-sm font-medium text-muted-foreground">
        {t("storage.overview")}
      </div>
      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <div className="flex-col rounded-lg bg-background_alt p-2.5 md:rounded-2xl">
          <div className="mb-5 flex flex-row items-center justify-between">
            {t("storage.recordings.title")}
            <Popover>
              <PopoverTrigger asChild>
                <button
                  className="focus:outline-none"
                  aria-label={t(
                    "storage.cameraStorage.unusedStorageInformation",
                  )}
                >
                  <CiCircleAlert
                    className="size-5"
                    aria-label={t(
                      "storage.cameraStorage.unusedStorageInformation",
                    )}
                  />
                </button>
              </PopoverTrigger>
              <PopoverContent className="w-80">
                <div className="space-y-2">{t("storage.recordings.tips")}</div>
              </PopoverContent>
            </Popover>
          </div>
          <StorageGraph
            graphId="general-recordings"
            used={totalStorage.camera}
            total={totalStorage.total}
          />
          {earliestDate && (
            <div className="mt-2 text-xs text-primary-variant">
              <span className="font-medium">
                {t("storage.recordings.earliestRecording")}
              </span>{" "}
              {formattedEarliestDate}
            </div>
          )}
        </div>
        <div className="flex-col rounded-lg bg-background_alt p-2.5 md:rounded-2xl">
          <div className="mb-5 truncate" title={storageEntries.cache.path}>
            {storageEntries.cache.path}
          </div>
          <StorageGraph
            graphId="general-cache"
            used={storageEntries.cache.stats.used}
            total={storageEntries.cache.stats.total}
          />
        </div>
        <div className="flex-col rounded-lg bg-background_alt p-2.5 md:rounded-2xl">
          <div className="mb-5 flex flex-row items-center justify-between">
            {isPosixSharedMemory ? t("storage.shm.posixTitle") : "/dev/shm"}
            <div className="flex flex-row items-center gap-2">
              {isPosixSharedMemory && (
                <Popover>
                  <PopoverTrigger asChild>
                    <button
                      className="focus:outline-none"
                      aria-label={t("storage.shm.posixInfo.title")}
                    >
                      <CiCircleAlert
                        className="size-5"
                        aria-label={t("storage.shm.posixInfo.title")}
                      />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80">
                    <div className="space-y-2">
                      <div className="font-medium">
                        {t("storage.shm.posixInfo.title")}
                      </div>
                      <div>{t("storage.shm.posixInfo.description")}</div>
                    </div>
                  </PopoverContent>
                </Popover>
              )}
              {shmFrameLifetime && (
                <Popover>
                  <PopoverTrigger asChild>
                    <button
                      className="focus:outline-none"
                      aria-label={t("storage.shm.frameLifetime.title")}
                    >
                      <CiCircleAlert
                        className="size-5"
                        aria-label={t("storage.shm.frameLifetime.title")}
                      />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80">
                    <div className="space-y-2">
                      {t("storage.shm.frameLifetime.description", {
                        frames: shmFrameLifetime.frames,
                        lifetime: shmFrameLifetime.lifetime,
                      })}
                    </div>
                  </PopoverContent>
                </Popover>
              )}
              {hasSharedMemoryWarning && (
                <Popover>
                  <PopoverTrigger asChild>
                    <button
                      className="focus:outline-none"
                      aria-label={t("storage.shm.title")}
                    >
                      <FaExclamationTriangle
                        className="size-5 text-danger"
                        aria-label={t("storage.shm.title")}
                      />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80">
                    <div className="space-y-2">
                      {isPosixSharedMemory
                        ? t("storage.shm.posixWarning", {
                            frames:
                              storageEntries.sharedMemory.stats.shm_frame_count,
                          })
                        : t("storage.shm.warning", {
                            total: storageEntries.sharedMemory.stats.total,
                            min_shm: storageEntries.sharedMemory.stats.min_shm,
                          })}
                      {!isPosixSharedMemory && (
                        <div className="mt-2 flex items-center text-primary">
                          <Link
                            to={getLocaleDocUrl(
                              "frigate/installation#calculating-required-shm-size",
                            )}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline"
                          >
                            {t("readTheDocumentation", { ns: "common" })}
                            <LuExternalLink className="ml-2 inline-flex size-3" />
                          </Link>
                        </div>
                      )}
                    </div>
                  </PopoverContent>
                </Popover>
              )}
            </div>
          </div>
          {storageEntries.sharedMemory.stats.metrics_available === false ? (
            <div className="flex h-[55px] items-center text-xs text-muted-foreground">
              {t("storage.shm.metricsUnavailable")}
            </div>
          ) : (
            <StorageGraph
              graphId="general-shared-memory"
              used={storageEntries.sharedMemory.stats.used}
              total={storageEntries.sharedMemory.stats.total}
            />
          )}
          {isPosixSharedMemory && (
            <div className="mt-2 text-xs text-primary-variant">
              {t("storage.shm.posixSummary", {
                objects: storageEntries.sharedMemory.stats.object_count ?? 0,
              })}
            </div>
          )}
        </div>
      </div>
      <div className="mt-4 text-sm font-medium text-muted-foreground">
        {t("storage.cameraStorage.title")}
      </div>
      <div className="mt-4 bg-background_alt p-2.5 md:rounded-2xl">
        <CombinedStorageGraph
          graphId={`single-storage`}
          cameraStorage={cameraStorage}
          totalStorage={totalStorage}
        />
      </div>
    </div>
  );
}
