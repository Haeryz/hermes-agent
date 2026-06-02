import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  ChevronRight,
  Clock,
  Database,
  Download,
  FileText,
  Monitor,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  CronJob,
  CronJobMonitor,
  ProfileInfo,
  PutusanFileState,
  PutusanMonitorStats,
} from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import {
  DEFAULT_SCHEDULE_STATE,
  ScheduleBuilder,
} from "@/components/ScheduleBuilder";
import {
  buildScheduleString,
  describeSchedule,
  englishOrdinal,
  type ScheduleBuilderState,
  type ScheduleDescribeStrings,
} from "@/lib/schedule";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { cn, themedBody } from "@/lib/utils";

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function formatCount(value?: number | null): string {
  return Number(value || 0).toLocaleString();
}

function formatBytes(value?: number | null): string {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}

function formatDelta(value?: number | null): string {
  const n = Number(value || 0);
  if (n > 0) return `+${formatCount(n)}`;
  return formatCount(n);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength
    ? value.slice(0, maxLength) + "..."
    : value;
}

function getJobPrompt(job: CronJob): string {
  return asText(job.prompt);
}

function getJobName(job: CronJob): string {
  return asText(job.name).trim();
}

function getJobTitle(job: CronJob): string {
  const name = getJobName(job);
  if (name) return name;

  const prompt = getJobPrompt(job);
  if (prompt) return truncateText(prompt, 60);

  const script = asText(job.script);
  if (script) return truncateText(script, 60);

  return job.id || "Cron job";
}

function getJobScheduleDisplay(
  job: CronJob,
  strings: ScheduleDescribeStrings,
): string {
  // Prefer a structured render so cron expressions like
  // ``30 14 * * 1,3,5`` surface as "Weekly on Mon, Wed, Fri at 14:30"
  // in the list instead of the raw five-field gibberish. Falls back
  // through the existing chain (``schedule_display`` from the backend,
  // then the structured ``display`` field, then the raw ``expr``) so
  // legacy job rows still render *something* meaningful.
  return describeSchedule(
    job.schedule,
    asText(job.schedule_display) || asText(job.schedule?.display),
    strings,
  );
}

function getJobState(job: CronJob): string {
  return asText(job.state) || (job.enabled === false ? "disabled" : "scheduled");
}

function getJobProfile(job: CronJob): string {
  return asText(job.profile) || asText(job.profile_name) || "default";
}

function getJobKey(job: CronJob): string {
  return `${getJobProfile(job)}:${job.id}`;
}

function splitJobKey(key: string): { profile: string; id: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { profile: "default", id: key };
  return { profile: key.slice(0, idx) || "default", id: key.slice(idx + 1) };
}

function profileLabel(profile: string): string {
  return profile === "default" ? "default" : profile;
}

function isPutusanCronJob(job: CronJob): boolean {
  const enabledToolsets = Array.isArray(job.enabled_toolsets)
    ? job.enabled_toolsets
    : [];
  const prompt = getJobPrompt(job);
  return (
    enabledToolsets.includes("putusan_crawler") ||
    getJobName(job).toLowerCase().startsWith("putusan crawler") ||
    prompt.includes("putusan_crawler") ||
    prompt.includes("Sinergi Putusan")
  );
}

const STATUS_TONE: Record<string, "success" | "warning" | "destructive"> = {
  enabled: "success",
  scheduled: "success",
  paused: "warning",
  error: "destructive",
  completed: "destructive",
};

type CreateMode = "general" | "putusan";
type PutusanAction = "download" | "count" | "stats" | "monitor";
type Meridiem = "AM" | "PM";

const DEFAULT_PUTUSAN_START_URL =
  "https://putusan3.mahkamahagung.go.id/direktori/index/kategori/peradilan-anak-abh-1.html";

function buildDailyCronFrom12Hour(
  hourText: string,
  minuteText: string,
  meridiem: Meridiem,
): string | null {
  const hour = Number.parseInt(hourText, 10);
  const minute = Number.parseInt(minuteText, 10);
  if (
    Number.isNaN(hour) ||
    Number.isNaN(minute) ||
    hour < 1 ||
    hour > 12 ||
    minute < 0 ||
    minute > 59
  ) {
    return null;
  }

  const hour24 = meridiem === "PM" ? (hour % 12) + 12 : hour % 12;
  return `${minute} ${hour24} * * *`;
}

function formatPutusanRunTime(
  hourText: string,
  minuteText: string,
  meridiem: Meridiem,
): string {
  const hour = Number.parseInt(hourText, 10);
  const minute = Number.parseInt(minuteText, 10);
  if (
    Number.isNaN(hour) ||
    Number.isNaN(minute) ||
    hour < 1 ||
    hour > 12 ||
    minute < 0 ||
    minute > 59
  ) {
    return "Invalid time";
  }
  return `${hour}:${String(minute).padStart(2, "0")} ${meridiem}`;
}

function buildPutusanPrompt(options: {
  action: PutusanAction;
  targetDownloads: string;
  maxCandidates: string;
  startUrl: string;
  outDir: string;
  headless: boolean;
  silentIfUnchanged: boolean;
}): string {
  const args: Record<string, unknown> = {
    action: options.action,
    out_dir: options.outDir.trim() || "downloads/kasus anak",
    start_url: options.startUrl.trim() || DEFAULT_PUTUSAN_START_URL,
    browser_backend: "managed-chrome",
    refresh_profile_snapshot: false,
    headless: options.headless,
    process_timeout_seconds: 1800,
    silent_if_unchanged: options.silentIfUnchanged,
  };

  if (options.action === "download") {
    args.target_downloads = Number.parseInt(options.targetDownloads, 10);
    args.parallel_downloads = 16;
  }

  const maxCandidates = options.maxCandidates.trim();
  if (maxCandidates) {
    args.max_candidates = Number.parseInt(maxCandidates, 10);
  }

  const crawlerArgs = JSON.stringify(args, null, 2);
  return [
    "Run the local Sinergi Putusan MA crawler for the scheduled Putusan monitoring workflow.",
    "",
    "1. Call putusan_crawler with these exact JSON arguments:",
    "```json",
    crawlerArgs,
    "```",
    "2. After that call completes, call putusan_crawler again with action='monitor' and the same out_dir.",
    "3. Report the crawler result plus aggregate monitoring data: downloaded total, unique detail URLs, skipped/error counts, PDF count, total PDF size, deltas since the last monitor snapshot, and latest downloads.",
    "4. Include any crawler failure, timeout, or missing output path clearly.",
    options.silentIfUnchanged
      ? "5. If the crawler succeeds, monitor delta is unchanged, and there are no new failures, respond with exactly [SILENT]."
      : "5. Always produce a concise status report, even when nothing changed.",
  ].join("\n");
}

interface MetricTileProps {
  label: string;
  value: string;
  delta?: number;
  tone?: "neutral" | "success" | "warning" | "destructive";
}

function MetricTile({
  label,
  value,
  delta,
  tone = "neutral",
}: MetricTileProps) {
  const toneClass = {
    neutral: "border-border bg-background/30",
    success: "border-success/30 bg-success/10",
    warning: "border-warning/30 bg-warning/10",
    destructive: "border-destructive/30 bg-destructive/10",
  }[tone];

  return (
    <div className={cn("grid gap-1 border p-3", toneClass)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[0.68rem] uppercase text-muted-foreground">
          {label}
        </span>
        {delta !== undefined && delta !== 0 && (
          <span className="text-[0.68rem] font-medium text-success">
            {formatDelta(delta)}
          </span>
        )}
      </div>
      <span className="font-mondwest text-display text-xl tracking-wider">
        {value}
      </span>
    </div>
  );
}

function BrowserViewportPanel({
  monitor,
  loading,
}: {
  monitor: CronJobMonitor | null;
  loading: boolean;
}) {
  const view = monitor?.browser_view;
  const liveLog = monitor?.live_log;
  const hasFrame = Boolean(view?.image_data_url);
  const statusLabel = hasFrame
    ? "visible"
    : loading
      ? "loading"
      : liveLog?.running
        ? "waiting"
        : "no frame";
  const address =
    view?.page_url ||
    view?.title ||
    liveLog?.action_name ||
    "putusan.mahkamahagung.go.id";
  const secondary =
    view?.captured_at
      ? `Captured ${formatTime(view.captured_at)}`
      : view?.error || liveLog?.lines?.[liveLog.lines.length - 1] || "";

  return (
    <section className="grid gap-2">
      <div className="flex items-center justify-between gap-2 text-xs uppercase text-muted-foreground">
        <span className="flex items-center gap-2">
          <Monitor className="h-3.5 w-3.5" />
          Live Chrome
        </span>
        <Badge tone={hasFrame ? "success" : liveLog?.running ? "warning" : "outline"}>
          {statusLabel}
        </Badge>
      </div>
      <div className="overflow-hidden border border-border bg-background/60">
        <div className="flex min-w-0 items-center gap-2 border-b border-border bg-muted/40 px-3 py-2">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-destructive/80" />
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-warning/80" />
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-success/80" />
          <span className="ml-2 min-w-0 flex-1 truncate border border-border bg-background/70 px-2 py-1 font-mono text-[0.66rem] text-muted-foreground">
            {address}
          </span>
        </div>
        <div className="relative aspect-video bg-white">
          {hasFrame ? (
            <img
              src={view?.image_data_url || ""}
              alt="Crawler Chrome viewport"
              className="h-full w-full bg-white object-contain"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 bg-white px-4 text-center text-sm text-muted-foreground">
              <Monitor className="h-7 w-7 text-muted-foreground/60" />
              <span>
                {loading
                  ? "Loading browser frame..."
                  : "Waiting for the crawler to capture its Chrome viewport."}
              </span>
            </div>
          )}
        </div>
      </div>
      {(secondary || view?.image_path) && (
        <div className="grid gap-1">
          {secondary && (
            <span className="line-clamp-2 text-xs text-muted-foreground">
              {secondary}
            </span>
          )}
          {view?.image_path && (
            <span className="truncate font-mono text-[0.64rem] text-muted-foreground">
              {view.image_path}
            </span>
          )}
        </div>
      )}
    </section>
  );
}

function PutusanMonitorPanel({
  job,
  monitor,
  loading,
  error,
  onRefresh,
  scheduleDescribeStrings,
}: {
  job: CronJob | null;
  monitor: CronJobMonitor | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  scheduleDescribeStrings: ScheduleDescribeStrings;
}) {
  const putusan = job ? isPutusanCronJob(job) : false;
  const stats: PutusanMonitorStats | null = monitor?.current ?? null;
  const delta = monitor?.delta ?? {};
  const statusTone: "success" | "warning" | "outline" = monitor?.running
    ? "success"
    : monitor?.active
      ? "warning"
      : "outline";
  const statusLabel = monitor?.status || "idle";

  return (
    <aside className="min-w-0 border border-border bg-card/70">
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
            <Activity className="h-3.5 w-3.5" />
            Live monitor
          </div>
          <h3 className="mt-1 truncate font-mondwest text-display text-lg tracking-wider">
            {job ? getJobTitle(job) : "Select a cron"}
          </h3>
        </div>
        <Button
          ghost
          size="icon"
          aria-label="Refresh monitor"
          title="Refresh monitor"
          disabled={!job || !putusan || loading}
          onClick={onRefresh}
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>

      {!job && (
        <div className="grid gap-3 p-5 text-sm text-muted-foreground">
          <div className="flex h-14 w-14 items-center justify-center border border-border bg-background/40">
            <ChevronRight className="h-5 w-5" />
          </div>
          <p>
            Click a cron job to inspect its schedule, status, and live crawler
            monitoring data.
          </p>
        </div>
      )}

      {job && !putusan && (
        <div className="grid gap-4 p-5">
          <div className="grid gap-2 text-sm">
            <span className="text-xs uppercase text-muted-foreground">
              Schedule
            </span>
            <span className="font-mono text-muted-foreground">
              {getJobScheduleDisplay(job, scheduleDescribeStrings)}
            </span>
          </div>
          <div className="border border-border bg-background/30 p-4 text-sm text-muted-foreground">
            Real-time monitoring is available for Putusan crawler cron jobs.
          </div>
        </div>
      )}

      {job && putusan && (
        <div className="grid gap-4 p-4">
          <div className="grid grid-cols-2 gap-3 text-xs text-muted-foreground">
            <div className="grid gap-1 border border-border bg-background/30 p-3">
              <span className="uppercase">Schedule</span>
              <span className="font-mono text-foreground">
                {getJobScheduleDisplay(job, scheduleDescribeStrings)}
              </span>
            </div>
            <div className="grid gap-1 border border-border bg-background/30 p-3">
              <span className="uppercase">Last refresh</span>
              <span className="text-foreground">
                {monitor?.refreshed_at ? formatTime(monitor.refreshed_at) : "No refresh"}
              </span>
            </div>
            <div className="grid gap-1 border border-border bg-background/30 p-3">
              <span className="uppercase">Runtime</span>
              <div className="flex min-w-0 items-center gap-2">
                <Badge tone={statusTone}>{statusLabel}</Badge>
                {job.no_agent && <Badge tone="outline">script</Badge>}
              </div>
            </div>
            <div className="grid gap-1 border border-border bg-background/30 p-3">
              <span className="uppercase">Activity</span>
              <span className="truncate text-foreground">
                {monitor?.latest_activity_at
                  ? formatTime(monitor.latest_activity_at)
                  : "No file activity"}
              </span>
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {monitor && !monitor.success && monitor.error && (
            <div className="flex items-start gap-2 border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{monitor.error}</span>
            </div>
          )}

          <BrowserViewportPanel monitor={monitor} loading={loading} />

          {!stats && !error && (
            <div className="flex items-center gap-3 border border-border bg-background/30 p-4 text-sm text-muted-foreground">
              <Spinner />
              Reading crawler output...
            </div>
          )}

          {stats && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <MetricTile
                  label="Downloaded"
                  value={formatCount(stats.downloaded_records)}
                  delta={delta.downloaded_records}
                  tone="success"
                />
                <MetricTile
                  label="PDF files"
                  value={formatCount(stats.pdf_files)}
                  delta={delta.pdf_files}
                  tone="success"
                />
                <MetricTile
                  label="Unique cases"
                  value={formatCount(stats.unique_detail_urls)}
                  delta={delta.unique_detail_urls}
                />
                <MetricTile
                  label="Skipped"
                  value={formatCount(stats.skipped_records)}
                  delta={delta.skipped_records}
                  tone={stats.skipped_records > 0 ? "warning" : "neutral"}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <MetricTile
                  label="PDF size"
                  value={formatBytes(stats.total_pdf_bytes)}
                  delta={delta.total_pdf_bytes}
                />
                <MetricTile
                  label="Missing paths"
                  value={formatCount(stats.missing_output_paths)}
                  delta={delta.missing_output_paths}
                  tone={stats.missing_output_paths > 0 ? "destructive" : "neutral"}
                />
              </div>

              <section className="grid gap-2">
                <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" />
                  Latest downloads
                </div>
                <div className="grid max-h-56 gap-2 overflow-y-auto pr-1">
                  {stats.latest_downloads.length === 0 && (
                    <div className="border border-border bg-background/30 p-3 text-sm text-muted-foreground">
                      No downloads recorded yet.
                    </div>
                  )}
                  {stats.latest_downloads.map((item, index) => (
                    <div
                      className="grid gap-1 border border-border/70 bg-background/30 p-3"
                      key={`${item.detail_url || item.output_path || index}`}
                    >
                      <span className="truncate text-sm font-medium">
                        {item.title || "Untitled Putusan record"}
                      </span>
                      <span className="truncate font-mono text-[0.68rem] text-muted-foreground">
                        {item.timestamp ? formatTime(item.timestamp) : "No timestamp"}
                      </span>
                    </div>
                  ))}
                </div>
              </section>

              <section className="grid gap-2">
                <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
                  <BarChart3 className="h-3.5 w-3.5" />
                  Recent events
                </div>
                <div className="grid max-h-52 gap-2 overflow-y-auto pr-1">
                  {stats.latest_events.length === 0 && (
                    <div className="border border-border bg-background/30 p-3 text-sm text-muted-foreground">
                      No crawler events recorded yet.
                    </div>
                  )}
                  {stats.latest_events.map((item, index) => (
                    <div
                      className="grid gap-1 border border-border/70 bg-background/30 p-3"
                      key={`${item.type || "event"}-${item.detail_url || item.output_path || index}`}
                    >
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">
                          {item.title || item.detail_url || "Putusan event"}
                        </span>
                        <Badge
                          tone={item.type === "downloaded" ? "success" : "warning"}
                        >
                          {item.status || item.type || "event"}
                        </Badge>
                      </div>
                      <span className="truncate font-mono text-[0.68rem] text-muted-foreground">
                        {item.timestamp ? formatTime(item.timestamp) : "No timestamp"}
                      </span>
                      {item.error && (
                        <span className="truncate text-xs text-warning">
                          {item.error}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </section>

              <section className="grid gap-2">
                <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
                  <Database className="h-3.5 w-3.5" />
                  Output files
                </div>
                <div className="grid gap-2">
                  {(
                    [
                      ["downloaded.jsonl", stats.files.downloaded_jsonl],
                      ["skipped.jsonl", stats.files.skipped_jsonl],
                      ["latest PDF", stats.files.latest_pdf],
                    ] satisfies Array<[string, PutusanFileState | null]>
                  ).map(([label, file]) => (
                    <div
                      className="grid gap-1 border border-border/70 bg-background/30 p-3"
                      key={String(label)}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium">{label}</span>
                        <Badge tone={file?.exists ? "success" : "outline"}>
                          {file?.exists ? formatBytes(file.size) : "missing"}
                        </Badge>
                      </div>
                      <span className="truncate font-mono text-[0.68rem] text-muted-foreground">
                        {file?.path || "not found"}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      )}
    </aside>
  );
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("all");
  const [loading, setLoading] = useState(true);
  const { toast, showToast } = useToast();
  const { t, locale } = useI18n();
  const { setEnd } = usePageHeader();

  // Translation surface for the human-readable schedule describer.
  // English ordinals are a special case ("1st", "2nd", "23rd"); every
  // other locale falls back to the plain numeric form, which avoids
  // shipping incorrect grammar (e.g. naive "1th"/"2th" suffixes that
  // don't exist in most languages).
  //
  // Built inline (not memoized) — the cron page renders a small job
  // list, this is single-digit microseconds, and a useMemo here would
  // just add boilerplate.
  const scheduleDescribeStrings: ScheduleDescribeStrings = {
    ...t.cron.scheduleDescribe,
    weekdaysShort: t.cron.scheduleModes.weekdaysShort,
    ordinal: locale === "en" ? englishOrdinal : (n: number) => String(n),
  };

  // New job modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  // The schedule is now constructed via the ScheduleBuilder; we keep
  // the full builder state so flipping between modes during edit
  // doesn't erase the user's intermediate inputs. The actual string
  // sent to the backend is derived via ``buildScheduleString`` at
  // submit time.
  const [scheduleState, setScheduleState] = useState<ScheduleBuilderState>(
    DEFAULT_SCHEDULE_STATE,
  );
  const [name, setName] = useState("");
  const [createMode, setCreateMode] = useState<CreateMode>("general");
  const [putusanAction, setPutusanAction] = useState<PutusanAction>("download");
  const [putusanTargetDownloads, setPutusanTargetDownloads] = useState("10");
  const [putusanMaxCandidates, setPutusanMaxCandidates] = useState("");
  const [putusanStartUrl, setPutusanStartUrl] = useState(DEFAULT_PUTUSAN_START_URL);
  const [putusanOutDir, setPutusanOutDir] = useState("downloads/kasus anak");
  const [putusanHeadless, setPutusanHeadless] = useState(true);
  const [putusanSilentIfUnchanged, setPutusanSilentIfUnchanged] = useState(true);
  const [putusanScheduleHour, setPutusanScheduleHour] = useState("12");
  const [putusanScheduleMinute, setPutusanScheduleMinute] = useState("00");
  const [putusanSchedulePeriod, setPutusanSchedulePeriod] = useState<Meridiem>("PM");
  const closeCreateModal = useCallback(() => setCreateModalOpen(false), []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
  });
  const [deliver, setDeliver] = useState("local");
  const [creating, setCreating] = useState(false);
  const createProfile = selectedProfile === "all" ? "default" : selectedProfile;

  // Edit job modal state
  const [editJob, setEditJob] = useState<CronJob | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editSchedule, setEditSchedule] = useState("");
  const [editName, setEditName] = useState("");
  const [editDeliver, setEditDeliver] = useState("local");
  const [saving, setSaving] = useState(false);
  const [selectedJobKey, setSelectedJobKey] = useState<string | null>(null);
  const [monitor, setMonitor] = useState<CronJobMonitor | null>(null);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState<string | null>(null);
  const closeEditModal = useCallback(() => setEditJob(null), []);
  const editModalRef = useModalBehavior({
    open: editJob !== null,
    onClose: closeEditModal,
  });

  const openEditModal = useCallback((job: CronJob) => {
    setEditJob(job);
    setEditPrompt(getJobPrompt(job));
    setEditSchedule(
      asText(job.schedule?.expr) || asText(job.schedule_display) || "",
    );
    setEditName(getJobName(job));
    setEditDeliver(asText(job.deliver) || "local");
  }, []);

  const loadJobs = useCallback(() => {
    api
      .getCronJobs(selectedProfile)
      .then(setJobs)
      .catch(() => showToast(t.common.loading, "error"))
      .finally(() => setLoading(false));
  }, [selectedProfile, showToast, t.common.loading]);

  useEffect(() => {
    api
      .getProfiles()
      .then((res) => setProfiles(res.profiles))
      .catch(() => setProfiles([]));
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  const selectedJob = selectedJobKey
    ? jobs.find((job) => getJobKey(job) === selectedJobKey) || null
    : null;

  useEffect(() => {
    if (selectedJobKey && jobs.some((job) => getJobKey(job) === selectedJobKey)) {
      return;
    }
    const firstPutusanJob = jobs.find(isPutusanCronJob);
    setSelectedJobKey(firstPutusanJob ? getJobKey(firstPutusanJob) : null);
  }, [jobs, selectedJobKey]);

  const loadMonitor = useCallback(
    async (showLoading = true) => {
      if (!selectedJob || !isPutusanCronJob(selectedJob)) {
        setMonitor(null);
        setMonitorError(null);
        setMonitorLoading(false);
        return;
      }

      if (showLoading) setMonitorLoading(true);
      try {
        const payload = await api.getCronJobMonitor(
          selectedJob.id,
          getJobProfile(selectedJob),
        );
        setMonitor(payload);
        setMonitorError(null);
      } catch (e) {
        setMonitorError(String(e));
      } finally {
        setMonitorLoading(false);
      }
    },
    [selectedJob],
  );

  useEffect(() => {
    if (!selectedJob) {
      return;
    }
    const firstRefresh = window.setTimeout(() => {
      void loadMonitor(false);
    }, 0);
    if (!isPutusanCronJob(selectedJob)) {
      return () => window.clearTimeout(firstRefresh);
    }
    const timer = window.setInterval(() => {
      void loadMonitor(false);
    }, 2000);
    return () => {
      window.clearTimeout(firstRefresh);
      window.clearInterval(timer);
    };
  }, [loadMonitor, selectedJob]);

  const scheduleString = buildScheduleString(scheduleState);

  const handleCreate = async () => {
    const cronSchedule =
      createMode === "putusan"
        ? buildDailyCronFrom12Hour(
            putusanScheduleHour,
            putusanScheduleMinute,
            putusanSchedulePeriod,
          )
        : scheduleString;

    if (!cronSchedule) {
      showToast(`${t.cron.schedule} required`, "error");
      return;
    }
    if (createMode === "general" && !prompt.trim()) {
      showToast(`${t.cron.prompt} & ${t.cron.schedule} required`, "error");
      return;
    }
    const targetDownloads = Number.parseInt(putusanTargetDownloads, 10);
    const maxCandidates = putusanMaxCandidates.trim()
      ? Number.parseInt(putusanMaxCandidates, 10)
      : null;
    if (
      createMode === "putusan" &&
      putusanAction === "download" &&
      (!Number.isFinite(targetDownloads) || targetDownloads <= 0)
    ) {
      showToast("Target downloads must be greater than 0", "error");
      return;
    }
    if (
      createMode === "putusan" &&
      putusanMaxCandidates.trim() &&
      (maxCandidates === null || Number.isNaN(maxCandidates) || maxCandidates <= 0)
    ) {
      showToast("Max candidates must be greater than 0", "error");
      return;
    }

    const cronPrompt =
      createMode === "putusan"
        ? buildPutusanPrompt({
            action: putusanAction,
            targetDownloads: putusanTargetDownloads,
            maxCandidates: putusanMaxCandidates,
            startUrl: putusanStartUrl,
            outDir: putusanOutDir,
            headless: putusanHeadless,
            silentIfUnchanged: putusanSilentIfUnchanged,
          })
        : prompt.trim();
    const cronName =
      name.trim() ||
      (createMode === "putusan" ? `Putusan crawler ${putusanAction}` : undefined);

    setCreating(true);
    try {
      await api.createCronJob(
        {
          prompt: cronPrompt,
          schedule: cronSchedule,
          name: cronName,
          deliver,
          enabled_toolsets:
            createMode === "putusan" ? ["putusan_crawler"] : undefined,
        },
        createProfile,
      );
      showToast(t.common.create + " ✓", "success");
      setPrompt("");
      setScheduleState(DEFAULT_SCHEDULE_STATE);
      setName("");
      setDeliver("local");
      setCreateMode("general");
      setCreateModalOpen(false);
      loadJobs();
    } catch (e) {
      showToast(`${t.config.failedToSave}: ${e}`, "error");
    } finally {
      setCreating(false);
    }
  };

  const handleEdit = async () => {
    if (!editJob) return;
    if (!editPrompt.trim() || !editSchedule.trim()) {
      showToast(`${t.cron.prompt} & ${t.cron.schedule} required`, "error");
      return;
    }
    setSaving(true);
    try {
      await api.updateCronJob(
        editJob.id,
        {
          prompt: editPrompt.trim(),
          schedule: editSchedule.trim(),
          name: editName.trim(),
          deliver: editDeliver,
        },
        getJobProfile(editJob),
      );
      showToast("Saved changes ✓", "success");
      setEditJob(null);
      loadJobs();
    } catch (e) {
      showToast(`${t.config.failedToSave}: ${e}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handlePauseResume = async (job: CronJob) => {
    try {
      const isPaused = getJobState(job) === "paused";
      const profile = getJobProfile(job);
      if (isPaused) {
        await api.resumeCronJob(job.id, profile);
        showToast(
          `${t.cron.resume}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      } else {
        await api.pauseCronJob(job.id, profile);
        showToast(
          `${t.cron.pause}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      }
      loadJobs();
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    }
  };

  const handleTrigger = async (job: CronJob) => {
    const putusan = isPutusanCronJob(job);
    if (putusan) {
      setSelectedJobKey(getJobKey(job));
      setMonitorLoading(true);
      setMonitorError(null);
    }
    try {
      const profile = getJobProfile(job);
      await api.triggerCronJob(job.id, profile);
      showToast(
        `${t.cron.triggerNow}: "${truncateText(getJobTitle(job), 30)}"`,
        "success",
      );
      if (putusan) {
        const payload = await api.getCronJobMonitor(job.id, profile);
        setMonitor(payload);
        setMonitorError(null);
        setMonitorLoading(false);
      }
      loadJobs();
    } catch (e) {
      if (putusan) {
        setMonitorLoading(false);
      }
      showToast(`${t.status.error}: ${e}`, "error");
    }
  };

  const jobDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const { profile, id } = splitJobKey(key);
        const job = jobs.find((j) => getJobKey(j) === key);
        try {
          await api.deleteCronJob(id, profile);
          showToast(
            `${t.common.delete}: "${job ? truncateText(getJobTitle(job), 30) : id}"`,
            "success",
          );
          loadJobs();
        } catch (e) {
          showToast(`${t.status.error}: ${e}`, "error");
          throw e;
        }
      },
      [jobs, loadJobs, showToast, t.common.delete, t.status.error],
    ),
  });

  // Put "Create" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={() => setCreateModalOpen(true)}
      >
        {t.common.create}
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t.common.create, loading]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const pendingJob = jobDelete.pendingId
    ? jobs.find((j) => getJobKey(j) === jobDelete.pendingId)
    : null;

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="cron:top" />
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={jobDelete.isOpen}
        onCancel={jobDelete.cancel}
        onConfirm={jobDelete.confirm}
        title={t.cron.confirmDeleteTitle}
        description={
          pendingJob
            ? `"${truncateText(getJobTitle(pendingJob), 40)}" — ${
                t.cron.confirmDeleteMessage
              }`
            : t.cron.confirmDeleteMessage
        }
        loading={jobDelete.isDeleting}
      />

      {/* Create job modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 backdrop-blur-sm p-4"
          onClick={(e) => e.target === e.currentTarget && setCreateModalOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-cron-title"
        >
          <div className={cn(themedBody, "relative w-full max-w-2xl border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setCreateModalOpen(false)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-cron-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                {t.cron.newJob}
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <div
                className="grid grid-cols-2 border border-border"
                role="tablist"
                aria-label="Cron job type"
              >
                <Button
                  ghost={createMode !== "general"}
                  className={cn(
                    "justify-center uppercase",
                    createMode === "general" &&
                      "bg-foreground text-background hover:bg-foreground/90",
                  )}
                  onClick={() => setCreateMode("general")}
                  role="tab"
                  aria-selected={createMode === "general"}
                >
                  <Clock className="h-3.5 w-3.5" />
                  General
                </Button>
                <Button
                  ghost={createMode !== "putusan"}
                  className={cn(
                    "justify-center uppercase",
                    createMode === "putusan" &&
                      "bg-foreground text-background hover:bg-foreground/90",
                  )}
                  onClick={() => setCreateMode("putusan")}
                  role="tab"
                  aria-selected={createMode === "putusan"}
                >
                  <Download className="h-3.5 w-3.5" />
                  Putusan crawler
                </Button>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="cron-profile">Profile</Label>
                <Select
                  id="cron-profile"
                  value={createProfile}
                  onValueChange={(v) => setSelectedProfile(v)}
                >
                  {profiles.map((profile) => (
                    <SelectOption key={profile.name} value={profile.name}>
                      {profileLabel(profile.name)}
                    </SelectOption>
                  ))}
                </Select>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="cron-name">{t.cron.nameOptional}</Label>
                <Input
                  id="cron-name"
                  autoFocus
                  placeholder={t.cron.namePlaceholder}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              {createMode === "general" ? (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor="cron-prompt">{t.cron.prompt}</Label>
                    <textarea
                      id="cron-prompt"
                      className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
                      placeholder={t.cron.promptPlaceholder}
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                    />
                  </div>

                  <ScheduleBuilder
                    value={scheduleState}
                    onChange={setScheduleState}
                  />
                </>
              ) : (
                <div className="grid gap-4 border border-border/70 bg-background/20 p-3">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Database className="h-4 w-4 shrink-0 text-foreground" />
                    <span>
                      Creates a scheduled job that runs the root Putusan crawler,
                      then records aggregate monitor deltas.
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="grid gap-2">
                      <Label htmlFor="putusan-action">Crawler action</Label>
                      <Select
                        id="putusan-action"
                        value={putusanAction}
                        onValueChange={(v) => setPutusanAction(v as PutusanAction)}
                      >
                        <SelectOption value="download">Download PDFs</SelectOption>
                        <SelectOption value="count">Count inventory</SelectOption>
                        <SelectOption value="stats">Stats only</SelectOption>
                        <SelectOption value="monitor">Monitor only</SelectOption>
                      </Select>
                    </div>

                    <div className="grid gap-2">
                      <Label htmlFor="putusan-target">Target downloads</Label>
                      <Input
                        id="putusan-target"
                        inputMode="numeric"
                        disabled={putusanAction !== "download"}
                        value={putusanTargetDownloads}
                        onChange={(e) => setPutusanTargetDownloads(e.target.value)}
                      />
                    </div>
                  </div>

                  <div className="grid gap-2">
                    <Label htmlFor="putusan-start-url">Start URL</Label>
                    <Input
                      id="putusan-start-url"
                      value={putusanStartUrl}
                      onChange={(e) => setPutusanStartUrl(e.target.value)}
                    />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="grid gap-2">
                      <Label htmlFor="putusan-out-dir">Output directory</Label>
                      <Input
                        id="putusan-out-dir"
                        value={putusanOutDir}
                        onChange={(e) => setPutusanOutDir(e.target.value)}
                      />
                    </div>

                    <div className="grid gap-2">
                      <Label htmlFor="putusan-max-candidates">Max candidates</Label>
                      <Input
                        id="putusan-max-candidates"
                        inputMode="numeric"
                        placeholder="Optional"
                        value={putusanMaxCandidates}
                        onChange={(e) => setPutusanMaxCandidates(e.target.value)}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <label className="flex items-center gap-2 text-sm text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={putusanHeadless}
                        onChange={(e) => setPutusanHeadless(e.target.checked)}
                      />
                      Headless browser
                    </label>
                    <label className="flex items-center gap-2 text-sm text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={putusanSilentIfUnchanged}
                        onChange={(e) =>
                          setPutusanSilentIfUnchanged(e.target.checked)
                        }
                      />
                      Silent when unchanged
                    </label>
                  </div>

                  <div className="grid gap-2">
                    <Label htmlFor="putusan-schedule-hour">Run time</Label>
                    <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
                      <Input
                        id="putusan-schedule-hour"
                        inputMode="numeric"
                        value={putusanScheduleHour}
                        onChange={(e) => setPutusanScheduleHour(e.target.value)}
                        aria-label="Putusan cron hour"
                      />
                      <Input
                        inputMode="numeric"
                        value={putusanScheduleMinute}
                        onChange={(e) => setPutusanScheduleMinute(e.target.value)}
                        aria-label="Putusan cron minute"
                      />
                      <Select
                        value={putusanSchedulePeriod}
                        onValueChange={(v) => setPutusanSchedulePeriod(v as Meridiem)}
                      >
                        <SelectOption value="AM">AM</SelectOption>
                        <SelectOption value="PM">PM</SelectOption>
                      </Select>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Daily at{" "}
                      {formatPutusanRunTime(
                        putusanScheduleHour,
                        putusanScheduleMinute,
                        putusanSchedulePeriod,
                      )}
                    </p>
                  </div>
                </div>
              )}

              <div className="grid gap-2">
                <Label htmlFor="cron-deliver">{t.cron.deliverTo}</Label>
                <Select
                  id="cron-deliver"
                  value={deliver}
                  onValueChange={(v) => setDeliver(v)}
                >
                  <SelectOption value="local">
                    {t.cron.delivery.local}
                  </SelectOption>
                  <SelectOption value="telegram">
                    {t.cron.delivery.telegram}
                  </SelectOption>
                  <SelectOption value="discord">
                    {t.cron.delivery.discord}
                  </SelectOption>
                  <SelectOption value="slack">
                    {t.cron.delivery.slack}
                  </SelectOption>
                  <SelectOption value="email">
                    {t.cron.delivery.email}
                  </SelectOption>
                </Select>
              </div>

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleCreate}
                  disabled={creating}
                  prefix={creating ? <Spinner /> : undefined}
                >
                  {creating ? t.common.creating : t.common.create}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit job modal */}
      {editJob && (
        <div
          ref={editModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 backdrop-blur-sm p-4"
          onClick={(e) => e.target === e.currentTarget && setEditJob(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-cron-title"
        >
          <div className={cn(themedBody, "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setEditJob(null)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="edit-cron-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                Edit job
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="edit-cron-name">{t.cron.nameOptional}</Label>
                <Input
                  id="edit-cron-name"
                  autoFocus
                  placeholder={t.cron.namePlaceholder}
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="edit-cron-prompt">{t.cron.prompt}</Label>
                <textarea
                  id="edit-cron-prompt"
                  className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
                  placeholder={t.cron.promptPlaceholder}
                  value={editPrompt}
                  onChange={(e) => setEditPrompt(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="edit-cron-schedule">{t.cron.schedule}</Label>
                  <Input
                    id="edit-cron-schedule"
                    placeholder={t.cron.schedulePlaceholder}
                    value={editSchedule}
                    onChange={(e) => setEditSchedule(e.target.value)}
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="edit-cron-deliver">{t.cron.deliverTo}</Label>
                  <Select
                    id="edit-cron-deliver"
                    value={editDeliver}
                    onValueChange={(v) => setEditDeliver(v)}
                  >
                    <SelectOption value="local">
                      {t.cron.delivery.local}
                    </SelectOption>
                    <SelectOption value="telegram">
                      {t.cron.delivery.telegram}
                    </SelectOption>
                    <SelectOption value="discord">
                      {t.cron.delivery.discord}
                    </SelectOption>
                    <SelectOption value="slack">
                      {t.cron.delivery.slack}
                    </SelectOption>
                    <SelectOption value="email">
                      {t.cron.delivery.email}
                    </SelectOption>
                  </Select>
                </div>
              </div>

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleEdit}
                  disabled={saving}
                  prefix={saving ? <Spinner /> : undefined}
                >
                  {saving ? t.common.loading : "Save changes"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <H2
            variant="sm"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <Clock className="h-4 w-4" />
            {t.cron.scheduledJobs} ({jobs.length})
          </H2>

          <div className="grid gap-1 min-w-[220px]">
            <Label htmlFor="cron-profile-filter">Profile</Label>
            <Select
              id="cron-profile-filter"
              value={selectedProfile}
              onValueChange={(v) => setSelectedProfile(v)}
            >
              <SelectOption value="all">All profiles</SelectOption>
              {profiles.map((profile) => (
                <SelectOption key={profile.name} value={profile.name}>
                  {profileLabel(profile.name)}
                </SelectOption>
              ))}
            </Select>
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,440px)]">
          <div className="grid gap-3">
            {jobs.length === 0 && (
              <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                  {t.cron.noJobs}
                </CardContent>
              </Card>
            )}

            {jobs.map((job) => {
              const state = getJobState(job);
              const promptText = getJobPrompt(job);
              const title = getJobTitle(job);
              const hasName = Boolean(getJobName(job));
              const deliver = asText(job.deliver);
              const profile = getJobProfile(job);
              const jobKey = getJobKey(job);
              const selected = selectedJobKey === jobKey;
              const putusan = isPutusanCronJob(job);
              const selectJob = () => {
                setSelectedJobKey(jobKey);
                setMonitorError(null);
                if (putusan) {
                  setMonitorLoading(true);
                } else {
                  setMonitor(null);
                  setMonitorLoading(false);
                }
              };

              return (
                <Card
                  key={jobKey}
                  className={cn(
                    "cursor-pointer transition-colors",
                    selected && "border-primary/60 bg-primary/5",
                  )}
                  onClick={selectJob}
                >
                  <CardContent className="flex items-start gap-4 py-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm truncate">
                          {title}
                        </span>
                        <Badge tone={STATUS_TONE[state] ?? "secondary"}>
                          {state}
                        </Badge>
                        <Badge tone="outline">{profileLabel(profile)}</Badge>
                        {putusan && <Badge tone="success">live monitor</Badge>}
                        {deliver && deliver !== "local" && (
                          <Badge tone="outline">{deliver}</Badge>
                        )}
                      </div>
                      {hasName && promptText && (
                        <p className="text-xs text-muted-foreground truncate mb-1">
                          {truncateText(promptText, 100)}
                        </p>
                      )}
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span className="font-mono-ui">
                          {getJobScheduleDisplay(job, scheduleDescribeStrings)}
                        </span>
                        <span>
                          {t.cron.last}: {formatTime(job.last_run_at)}
                        </span>
                        <span>
                          {t.cron.next}: {formatTime(job.next_run_at)}
                        </span>
                      </div>
                      {job.last_error && (
                        <p className="text-xs text-destructive mt-1">
                          {job.last_error}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        ghost
                        size="icon"
                        title={state === "paused" ? t.cron.resume : t.cron.pause}
                        aria-label={
                          state === "paused" ? t.cron.resume : t.cron.pause
                        }
                        onClick={(event) => {
                          event.stopPropagation();
                          void handlePauseResume(job);
                        }}
                        className={
                          state === "paused" ? "text-success" : "text-warning"
                        }
                      >
                        {state === "paused" ? <Play /> : <Pause />}
                      </Button>

                      <Button
                        ghost
                        size="icon"
                        title={t.cron.triggerNow}
                        aria-label={t.cron.triggerNow}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleTrigger(job);
                        }}
                      >
                        <Zap />
                      </Button>

                      <Button
                        ghost
                        size="icon"
                        title="Edit job"
                        aria-label="Edit job"
                        onClick={(event) => {
                          event.stopPropagation();
                          openEditModal(job);
                        }}
                      >
                        <Pencil />
                      </Button>

                      <Button
                        ghost
                        destructive
                        size="icon"
                        title={t.common.delete}
                        aria-label={t.common.delete}
                        onClick={(event) => {
                          event.stopPropagation();
                          jobDelete.requestDelete(jobKey);
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <PutusanMonitorPanel
            job={selectedJob}
            monitor={monitor}
            loading={monitorLoading}
            error={monitorError}
            onRefresh={() => void loadMonitor(true)}
            scheduleDescribeStrings={scheduleDescribeStrings}
          />
        </div>
      </div>

      <PluginSlot name="cron:bottom" />
    </div>
  );
}
