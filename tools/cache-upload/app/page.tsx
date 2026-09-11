"use client";
import { useEffect, useRef, useState } from "react";
import { parseReceiptHash } from "../lib/receipt.mjs";
import prepareWorkerUrl from "../lib/prepare.worker.mjs?worker&url";
import {
  FolderOpen,
  ShieldCheck,
  Archive,
  Flame,
  ExternalLink,
  MessageCircle,
  CheckCircle2,
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  MODES,
  POLICY_VERSION,
  TOTAL_LIMIT,
  FILE_COUNT,
  canonicalName,
  allowedName,
} from "@/shared/policy.mjs";
import {
  groupInstallationFiles,
  findInstallationFolders,
  readGameFolder,
} from "@/lib/game-folders.mjs";
type FolderKind = "wdb" | "account";
type LocalFile = { file: File; path: string };
type Prepared = {
  name: string;
  mode: string;
  size: number;
  sha256: string;
  review: boolean;
  data: Blob;
};
type Receipt = { id: string; receiptToken: string; uploadToken?: string };
declare global {
  interface Window {
    turnstile?: {
      render: (target: HTMLElement, options: Record<string, unknown>) => string;
      reset: (id?: string) => void;
      remove: (id: string) => void;
    };
  }
}
const size = (n: number) => (n / 1048576).toFixed(1) + " MiB";
async function api(path: string, options: RequestInit = {}): Promise<any> {
  const response = await fetch("/api/" + path, {
    ...options,
    cache: "no-store",
  });
  const result: any = await response.json();
  if (!response.ok)
    throw Error(result.error || "The service could not complete this request");
  return result;
}
export default function Home() {
  const [files, setFiles] = useState<Prepared[]>([]),
    [skipped, setSkipped] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [phase, setPhase] = useState(""),
    [error, setError] = useState(""),
    [selectionNotice, setSelectionNotice] = useState(""),
    [consent, setConsent] = useState(false),
    [progress, setProgress] = useState(0),
    [config, setConfig] = useState<{
      enabled: boolean;
      siteKey: string;
    } | null>(null),
    [verification, setVerification] = useState(""),
    [receipt, setReceipt] = useState<Receipt | null>(null),
    [status, setStatus] = useState<any>(null),
    [receiptError, setReceiptError] = useState(""),
    [receiptRefresh, setReceiptRefresh] = useState(0),
    [receiptLoading, setReceiptLoading] = useState(false),
    [receiptChecked, setReceiptChecked] = useState(""),
    [fallback, setFallback] = useState("unknown"),
    [gameLocation, setGameLocation] = useState(false),
    [folderLabels, setFolderLabels] = useState<
      Partial<Record<FolderKind, string>>
    >({});
  const receiptPanel = useRef<HTMLDivElement>(null),
    installation = useRef<HTMLInputElement>(null),
    replacementFolders = useRef<
      Partial<Record<FolderKind, { folder?: any; files: LocalFile[] }>>
    >({}),
    installationFolders = useRef<any>(null),
    installationFiles = useRef<Record<FolderKind, LocalFile[]> | null>(null),
    folder = useRef<HTMLInputElement>(null),
    accountFolder = useRef<HTMLInputElement>(null),
    zip = useRef<HTMLInputElement>(null),
    loose = useRef<HTMLInputElement>(null),
    captcha = useRef<HTMLDivElement>(null),
    widget = useRef<string | undefined>(undefined),
    pending = useRef<Receipt | null>(null);
  useEffect(() => {
    api("config")
      .then(setConfig)
      .catch(() =>
        setError(
          "The contribution service is unavailable. You can still preview your files.",
        ),
      );
    const readReceipt = () => {
      setStatus(null);
      setReceiptError("");
      setReceiptChecked("");
      try {
        setReceipt(parseReceiptHash(location.hash));
      } catch (e: any) {
        setReceipt(null);
        setReceiptError(e.message);
      }
    };
    readReceipt();
    window.addEventListener("hashchange", readReceipt);
    return () => window.removeEventListener("hashchange", readReceipt);
  }, []);
  useEffect(() => {
    if (!config?.enabled || !config.siteKey || !captcha.current) return;
    let dead = false;
    let script: HTMLScriptElement | undefined;
    const render = () => {
      if (
        !dead &&
        captcha.current &&
        window.turnstile &&
        widget.current === undefined
      )
        widget.current = window.turnstile.render(captcha.current, {
          sitekey: config.siteKey,
          action: "contribute",
          theme: "dark",
          callback: (token: string) => setVerification(token),
          "expired-callback": () => setVerification(""),
          "error-callback": () => setVerification(""),
        });
    };
    if (window.turnstile) render();
    else {
      script = document.createElement("script");
      script.src =
        "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true;
      script.onload = render;
      document.head.appendChild(script);
    }
    return () => {
      dead = true;
      if (widget.current) window.turnstile?.remove(widget.current);
      widget.current = undefined;
      script?.remove();
    };
  }, [config]);
  useEffect(() => {
    if (!receipt) return;
    let active = true;
    const refresh = () => {
      if (active) setReceiptLoading(true);
      return api(`submissions/${receipt.id}/status`, {
        headers: { Authorization: "Bearer " + receipt.receiptToken },
      })
        .then((s) => {
          if (active) {
            setStatus(s);
            setReceiptError("");
            setReceiptChecked(new Date().toLocaleTimeString());
          }
        })
        .catch((e) => {
          if (active) setReceiptError(e.message);
        })
        .finally(() => {
          if (active) setReceiptLoading(false);
        });
    };
    refresh();
    const t = setInterval(refresh, 30000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [receipt, receiptRefresh]);
  useEffect(() => {
    if (receipt || receiptError) {
      receiptPanel.current?.scrollIntoView({ block: "start" });
      receiptPanel.current?.focus({ preventScroll: true });
    }
  }, [receipt]);
  useEffect(() => {
    const context = (document as any).modelContext;
    if (!context?.registerTool) return;
    const life = new AbortController();
    Promise.resolve(
      context.registerTool(
        {
          name: "read_contribution_preview",
          title: "Read contribution preview",
          description:
            "Read the selected sanitized game-file counts and receipt state. Does not read local files or upload anything.",
          inputSchema: {
            type: "object",
            properties: {},
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: true },
          execute: (input: unknown) => {
            if (
              !input ||
              typeof input !== "object" ||
              Object.keys(input).length
            )
              throw Error("Expected an empty object");
            return {
              fileParts: files.length,
              bytes: files.reduce((s, f) => s + f.size, 0),
              reviewFiles: files.filter((f) => f.review).length,
              excluded: skipped.length,
              status: status?.status || "preview",
            };
          },
        },
        { signal: life.signal },
      ),
    ).catch(() => {});
    return () => life.abort();
  }, [files, skipped, status]);
  function useInstallationFiles(selection: FileList | null) {
    if (!selection?.length) return;
    const groups = groupInstallationFiles(Array.from(selection));
    if (!groups.wdb.length && !groups.account.length) {
      setError(
        "No supported files found under Cache/WDB or WTF/Account. Choose the ascension-live folder, or add a folder directly.",
      );
      return;
    }
    replacementFolders.current = {};
    setFolderLabels({});
    installationFiles.current = groups;
    installationFolders.current = null;
    setGameLocation(true);
    setError("");
    if (installation.current) installation.current.value = "";
  }
  async function selectInstallation() {
    const picker = (window as any).showDirectoryPicker;
    if (!picker) {
      installation.current?.click();
      return;
    }
    try {
      const root = await picker.call(window, {
        id: "ascension-installation",
        mode: "read",
      });
      setBusy(true);
      const groups = await findInstallationFolders(root);
      if (!groups.wdb && !groups.account)
        throw Error(
          "Choose the ascension-live folder containing Cache and WTF, or add a folder directly.",
        );
      replacementFolders.current = {};
      setFolderLabels({});
      installationFolders.current = groups;
      installationFiles.current = null;
      setGameLocation(true);
      setError("");
    } catch (e: any) {
      if (e.name !== "AbortError")
        setError(e.message || "Could not open the game installation.");
    } finally {
      setBusy(false);
    }
  }
  async function chooseReplacement(
    selection: FileList | LocalFile[] | null,
    sourceFolder?: any,
  ) {
    if (!selection?.length) return;
    const entries: LocalFile[] = Array.from(selection as any).map(
      (entry: any) =>
        entry.file
          ? entry
          : { file: entry, path: entry.webkitRelativePath || entry.name },
    );
    const labels: Partial<Record<FolderKind, string>> = {};
    for (const kind of ["wdb", "account"] as FolderKind[]) {
      const selected = entries.filter(({ path }) => {
        const name = canonicalName(path);
        return allowedName(name) && name.endsWith(".wdb") === (kind === "wdb");
      });
      if (selected.length) {
        replacementFolders.current[kind] = {
          folder: sourceFolder,
          files: selected,
        };
        labels[kind] = sourceFolder?.path || selected[0].path.split("/")[0];
      }
    }
    setFolderLabels((old) => ({ ...old, ...labels }));
    await choose(entries);
  }
  async function chooseDifferentFolder() {
    const picker = (window as any).showDirectoryPicker;
    if (!picker) {
      folder.current?.click();
      return;
    }
    try {
      const handle = await picker.call(window, {
        id: "ascension-other-folder",
        mode: "read",
      });
      setBusy(true);
      setError("");
      setPhase("Finding useful files in your selected folder…");
      const selection = await readGameFolder({ handle, path: handle.name });
      if (!selection.length)
        throw Error(
          "No supported files found in this folder. Choose WDB or Account, including its subfolders.",
        );
      await chooseReplacement(selection, { handle, path: handle.name });
    } catch (e: any) {
      if (e.name !== "AbortError")
        setError(
          e.message ||
            "Could not open this folder. Try choosing individual files below.",
        );
    } finally {
      setBusy(false);
      setPhase("");
    }
  }
  async function chooseGameFolder(kind: FolderKind) {
    const replacement = replacementFolders.current[kind];
    if (!gameLocation && !replacement) {
      (kind === "wdb" ? folder : accountFolder).current?.click();
      return;
    }
    setBusy(true);
    setError("");
    setPhase("Finding useful files in your game folder…");
    try {
      const source = installationFolders.current?.[kind];
      const selection = replacement
        ? replacement.folder
          ? (await readGameFolder(replacement.folder)).filter(
              ({ path }: LocalFile) =>
                canonicalName(path).endsWith(".wdb") === (kind === "wdb"),
            )
          : replacement.files
        : (installationFiles.current?.[kind] ??
          (source ? await readGameFolder(source) : []));
      if (!selection.length)
        throw Error(
          kind === "account"
            ? "No recognized addon capture files found in this Account folder. See Supported Account files below, or choose a different folder."
            : "No supported WDB cache files found here. Choose a different folder below.",
        );
      await choose(selection);
    } catch (e: any) {
      setError(
        e.message ||
          "Could not read this folder. Choose it again to renew access.",
      );
    } finally {
      setBusy(false);
      setPhase("");
    }
  }
  async function choose(selection: FileList | File[] | LocalFile[] | null) {
    if (!selection?.length) return;
    setBusy(true);
    setError("");
    setPhase("Selecting useful game data on your device…");
    pending.current = null;
    setConsent(false);
    let worker: Worker | undefined;
    try {
      worker = new Worker(prepareWorkerUrl, { type: "module" });
      const activeWorker = worker;
      const prepared: any = await new Promise((resolve, reject) => {
        const timeout = () =>
          reject(
            Error(
              "File analysis stopped responding. Try selecting the folder again.",
            ),
          );
        let timer = setTimeout(timeout, 60000);
        activeWorker.onmessage = (e) => {
          clearTimeout(timer);
          if (e.data.progress) {
            setPhase(
              `Preparing useful game data: ${size(e.data.preparedBytes)}…`,
            );
            timer = setTimeout(timeout, 60000);
            return;
          }
          e.data.error ? reject(Error(e.data.error)) : resolve(e.data);
        };
        activeWorker.onerror = () => {
          clearTimeout(timer);
          reject(
            Error(
              "The file processor could not start or stopped unexpectedly. Refresh the page and select your folder again.",
            ),
          );
        };
        activeWorker.postMessage({ files: [...selection] });
      });
      const seen = new Set(files.map((f) => f.sha256 + f.mode));
      const added: Prepared[] = prepared.files.filter((f: Prepared) => {
        const key = f.sha256 + f.mode;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      const next = [...files, ...added];
      if (
        next.length > FILE_COUNT ||
        next.reduce((sum, f) => sum + f.size, 0) > TOTAL_LIMIT
      )
        throw Error(
          "Combined selection exceeds 512 MiB or 256 file parts. Clear the current selection before adding another folder.",
        );
      setFiles(next);
      setSelectionNotice(
        added.length
          ? `Added ${added.length} game file parts (${size(added.reduce((sum, f) => sum + f.size, 0))}) from your selection.`
          : prepared.files.length
            ? "These game files are already in your selection. No duplicate upload is needed."
            : "No usable game data found in this selection. See the excluded-file details below.",
      );
      setSkipped((old) => [...old, ...prepared.skipped]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      worker?.terminate();
      setBusy(false);
      setPhase("");
      if (folder.current) folder.current.value = "";
      if (accountFolder.current) accountFolder.current.value = "";
      if (zip.current) zip.current.value = "";
      if (loose.current) loose.current.value = "";
    }
  }
  async function upload() {
    setBusy(true);
    setError("");
    setProgress(0);
    try {
      let session = pending.current;
      if (!session) {
        setPhase("Verifying your submission…");
        session = await api("submissions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            policy: POLICY_VERSION,
            consent,
            turnstile: verification,
            files: files.map(({ data, ...f }) => ({
              ...f,
              mode: f.mode === "unknown" ? fallback : f.mode,
            })),
          }),
        });
        pending.current = session;
        setVerification("");
        window.turnstile?.reset(widget.current);
      }
      for (let i = 0; i < files.length; i++) {
        setPhase(`Uploading file part ${i + 1} of ${files.length}…`);
        await api(`submissions/${session!.id}/files/${i}`, {
          method: "PUT",
          headers: {
            Authorization: "Bearer " + session!.uploadToken,
            "Content-Type": "application/octet-stream",
          },
          body: files[i].data as BodyInit,
        });
        setProgress(Math.round(((i + 1) / files.length) * 100));
      }
      await api(`submissions/${session!.id}/complete`, {
        method: "POST",
        headers: { Authorization: "Bearer " + session!.uploadToken },
      });
      setReceipt(session);
      history.replaceState(
        null,
        "",
        "#receipt=" + session!.id + "&token=" + session!.receiptToken,
      );
      setFiles([]);
      pending.current = null;
      setPhase("Received. Your files are waiting to be processed.");
    } catch (e: any) {
      setError(
        e.message + " Your originals are unchanged. You can retry this upload.",
      );
      if (!pending.current) {
        setVerification("");
        window.turnstile?.reset(widget.current);
      }
    } finally {
      setBusy(false);
    }
  }
  const total = files.reduce((s, f) => s + f.size, 0),
    review = files.filter((f) => f.review).length;
  return (
    <main className="shell">
      <header>
        <a
          href="https://github.com/hertigservices/ascension-data"
          className="brand"
        >
          <Flame size={27} aria-hidden="true" /> ASCENSION <span>ARCHIVE</span>
        </a>
        <span>Community preservation</span>
      </header>
      {(receipt || receiptError) && (
        <div
          className="notice receipt"
          ref={receiptPanel}
          tabIndex={-1}
          aria-labelledby="receipt-title"
        >
          <CheckCircle2 size={22} />
          <h2 id="receipt-title">Your contribution receipt</h2>
          {receiptError && <p role="alert">{receiptError}</p>}
          {receipt && (
            <>
              <p role="status" aria-live="polite">
                {status?.message ||
                  (
                    {
                      uploading: "Upload in progress",
                      received: "Received — queued for processing",
                      processing: "Being validated and merged",
                      needs_review: "Waiting for maintainer review",
                      published: "Published to the archive",
                    } as any
                  )[status?.status] ||
                  "Loading receipt…"}
              </p>
              {status && (
                <p>
                  {status.files} file parts received
                  {status.reviewFiles
                    ? `; ${status.reviewFiles} require maintainer review`
                    : ""}
                  .
                </p>
              )}
              <button
                type="button"
                className="plain"
                disabled={receiptLoading}
                onClick={() => setReceiptRefresh((n) => n + 1)}
              >
                {receiptLoading ? "Checking status..." : "Refresh status"}
              </button>
              <p className="hint">
                Updates automatically every 30 seconds.
                {receiptChecked && ` Last checked ${receiptChecked}.`}
              </p>
              <a
                onClick={() => {
                  receiptPanel.current?.scrollIntoView({ block: "start" });
                  setReceiptRefresh((n) => n + 1);
                }}
                href={
                  "#receipt=" + receipt.id + "&token=" + receipt.receiptToken
                }
              >
                Link to this receipt
              </a>
              <small>
                Save this link or bookmark this page to return later. Anyone
                with the link can view its status, but cannot access your files.
              </small>
              {status?.commit && (
                <a
                  href={
                    "https://github.com/hertigservices/ascension-data/commit/" +
                    status.commit
                  }
                >
                  View the published result ↗
                </a>
              )}
            </>
          )}
        </div>
      )}
      <div className="intro">
        <div className="archive-seal" aria-hidden="true">
          <div className="seal-ring">
            <Flame strokeWidth={1} />
          </div>
          <span>KEEP THE WORLD ALIVE</span>
        </div>
        <p className="eyebrow">ASCENSION COMMUNITY PRESERVATION</p>
        <h1>
          Your adventures.
          <br />
          <em>A world worth keeping.</em>
        </h1>
        <p>
          Contribute game data from your WDB or Account folder.
          <br />
          No installation. No account. Your originals stay untouched.
        </p>
      </div>
      <section
        className="contributor-note"
        aria-labelledby="contributor-note-title"
      >
        <CheckCircle2 size={23} aria-hidden="true" />
        <div>
          <h2 id="contributor-note-title">
            Already contributed on Discord? You’re all set.
          </h2>
          <p>
            If you already shared your files in the Conquest of AzerothCore
            Discord, <strong>you do not need to upload them again.</strong>{" "}
            Thank you for helping preserve Ascension.
          </p>
        </div>
      </section>
      <div className="workspace">
        <section className="panel">
          <div className="step">
            01 <span>Choose your files</span>
          </div>
          <div className="game-location">
            <div className="installation-choice">
              <button
                className="location-button"
                aria-describedby="install-location-hint"
                disabled={busy}
                onClick={selectInstallation}
              >
                <FolderOpen size={18} aria-hidden="true" />
                {gameLocation
                  ? "Change game installation"
                  : "Choose game installation"}
              </button>
              <div className="install-location-hint" id="install-location-hint">
                <span>Common Windows locations</span>
                <code>{String.raw`C:\Program Files\Ascension Launcher\resources\ascension-live`}</code>
                <code>{String.raw`C:\Ascension\Launcher\resources\ascension-live`}</code>
              </div>
            </div>
            <p>
              {gameLocation
                ? "Game folder selected for this visit. Click WDB or Account below to add its useful files."
                : "Optional: select ascension-live once, then use both folder buttons without browsing back."}
            </p>
            <small>
              Look inside your Ascension Launcher folder:{" "}
              <code>resources → ascension-live</code>. Only supported cache and
              addon files are prepared for upload.
            </small>
          </div>
          <input
            ref={installation}
            type="file"
            multiple
            {...({ webkitdirectory: "" } as any)}
            className="file-input"
            tabIndex={-1}
            aria-label="Choose the ascension-live game installation"
            onChange={(e) => useInstallationFiles(e.target.files)}
          />
          <input
            ref={accountFolder}
            type="file"
            multiple
            {...({ webkitdirectory: "" } as any)}
            className="file-input"
            tabIndex={-1}
            aria-label="Choose an Account folder"
            onChange={(e) => chooseReplacement(e.target.files)}
          />
          <input
            ref={folder}
            type="file"
            multiple
            {...({ webkitdirectory: "" } as any)}
            className="file-input"
            tabIndex={-1}
            aria-label="Choose a WDB folder"
            onChange={(e) => chooseReplacement(e.target.files)}
          />
          <input
            ref={zip}
            type="file"
            accept=".zip"
            multiple
            className="file-input"
            tabIndex={-1}
            aria-label="Choose ZIP archives"
            onChange={(e) => choose(e.target.files)}
          />
          <input
            ref={loose}
            type="file"
            accept=".wdb,.lua,.bak"
            multiple
            className="file-input"
            tabIndex={-1}
            aria-label="Choose individual cache files"
            onChange={(e) => choose(e.target.files)}
          />
          <div className="choices">
            <button disabled={busy} onClick={() => chooseGameFolder("wdb")}>
              <FolderOpen />
              Add WDB folder<small>Items, creatures, quests and more</small>
              <span className="folder-path">
                {folderLabels.wdb
                  ? `Selected: ${folderLabels.wdb}`
                  : "ascension-live / Cache / WDB"}
              </span>
            </button>
            <button disabled={busy} onClick={() => chooseGameFolder("account")}>
              <FolderOpen />
              Add Account folder<small>Useful data saved by your addons</small>
              <span className="folder-path">
                {folderLabels.account
                  ? `Selected: ${folderLabels.account}`
                  : "ascension-live / WTF / Account"}
              </span>
            </button>
          </div>
          <button
            className="plain different-folder"
            disabled={busy}
            onClick={chooseDifferentFolder}
          >
            Choose a different WDB or Account folder
          </button>
          <p className="hint">
            Select the actual WDB or Account folder, including its subfolders.
            Use Choose game installation only when selecting the ascension-live
            root. Desktop copies and backups work too. Up to 512 MiB of prepared
            game data per submission; large WDB files are split automatically.
          </p>
          {selectionNotice && (
            <p className="notice" role="status">
              {selectionNotice}
            </p>
          )}
          <button
            disabled={busy}
            className="zip"
            onClick={() => zip.current?.click()}
          >
            <Archive size={18} /> Already have a ZIP? Choose it here
          </button>
          <button
            disabled={busy}
            className="plain"
            onClick={() => loose.current?.click()}
          >
            Or choose individual files
          </button>
          {!config?.enabled && (
            <div className="notice">
              Uploads are not open yet. You can select files and preview what
              would be contributed; nothing will be sent.
            </div>
          )}
          {files.length ? (
            <>
              <div className="summary">
                <strong>{files.length} game file parts</strong> · {size(total)}
                {review > 0 && (
                  <p className="review">
                    {review} addon files will be held for maintainer review.
                  </p>
                )}
                <ul className="file-list">
                  {files.map((f, i) => (
                    <li key={f.sha256 + f.mode + i}>
                      {f.name}
                      <small>
                        {size(f.size)} ·{" "}
                        {f.mode === "unknown" ? "Realm not identified" : f.mode}
                        {f.review ? " · Review required" : ""}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>
              {files.some((f) => f.mode === "unknown") && (
                <div className="realm">
                  <label id="realm-label">Unidentified files belong to</label>
                  <Select
                    value={fallback}
                    onValueChange={(v) => {
                      setFallback(v);
                      pending.current = null;
                    }}
                    disabled={busy}
                  >
                    <SelectTrigger aria-labelledby="realm-label">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {MODES.map((mode: string) => (
                        <SelectItem value={mode} key={mode}>
                          {mode === "unknown"
                            ? "I'm not sure"
                            : mode.replaceAll("-", " ")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <small>
                    Only choose a mode if all unidentified files came from it.
                  </small>
                </div>
              )}
              <label className="consent">
                <Checkbox
                  checked={consent}
                  onCheckedChange={(v) => setConsent(v === true)}
                  disabled={busy}
                />
                <span>
                  I agree to contribute the selected game data to the public
                  preservation dataset. Private upload copies expire after seven
                  days. Accepted game data is retained in the archive and may
                  remain in Git history.
                </span>
              </label>
              <div className="actions">
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !consent ||
                    !config?.enabled ||
                    (!verification && !pending.current)
                  }
                  onClick={upload}
                >
                  {pending.current
                    ? "Retry contribution"
                    : "Contribute game data"}
                </button>
                <button
                  className="plain"
                  disabled={busy}
                  onClick={() => {
                    setFiles([]);
                    setSkipped([]);
                    pending.current = null;
                    setConsent(false);
                  }}
                >
                  Clear selection
                </button>
              </div>
            </>
          ) : (
            <div className="empty">
              Your selected game files will appear here.
              <br />
              <small>You can contribute either folder, or both.</small>
            </div>
          )}
          {skipped.length > 0 && (
            <details>
              <summary>{skipped.length} exclusions or notices</summary>
              <ul>
                {Array.from(new Set(skipped))
                  .slice(0, 50)
                  .map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
              </ul>
            </details>
          )}
          <div ref={captcha} className="status" />
          {busy && <Progress value={progress} aria-label="Upload progress" />}
          <div role="status" aria-live="polite" className="status">
            {phase}
          </div>
          {error && (
            <div role="alert" className="notice error">
              {error}
            </div>
          )}
        </section>
        <aside>
          <ShieldCheck size={30} />
          <h2>Contribute with confidence</h2>
          <p>
            Supported game data is selected on your device. Account paths,
            character profiles and unrelated settings are excluded.
          </p>
          <p>
            Submitted files stay private. Only reviewed or validated game data
            reaches the public archive.
          </p>
          <details>
            <summary>What is useful?</summary>
            <p>
              Seven game-query WDB cache types, mob observations, gather
              locations, auction prices, AIO addon data, CoA events and
              WildcardHarvest references, plus CoAReader class trees and
              AscensionHarvest game references for automatic preservation. Mail
              caches and unrelated Account files are excluded.
            </p>
          </details>
          <details>
            <summary>Supported Account files</summary>
            <p>
              MobSpells, GatherMate2, Auctionator_Price_Database, AIO_Client,
              CoASniff, WildcardHarvest, Ascension_CoAReader and
              AscensionHarvest Lua files, including .lua.bak backups.
            </p>
            <p>
              CoAReader contributes class-tree references. AscensionHarvest
              contributes item, quest and spell references. These two formats
              are privacy-filtered and preserved automatically as deduplicated
              reference snapshots. Conflicting snapshots are kept separately. UI
              settings, chat, guild messages and personal character data are
              excluded.
            </p>
          </details>
          <details>
            <summary>Where are my folders?</summary>
            <p>
              Inside your game installation, look for Cache → WDB and WTF →
              Account. Close the game before selecting them so the latest data
              is saved. You choose what the browser can read.
            </p>
          </details>
          <details>
            <summary>Privacy and retention</summary>
            <p>
              Filtering reduces personal data before upload. It cannot guarantee
              that every free-text field is anonymous. The server validates
              again, and the consolidator checks publication. Private upload and
              review copies expire after seven days. Accepted game data is
              retained for preservation, including in the consolidator and
              published dataset. No original folder paths or account names are
              sent as metadata.
            </p>
          </details>
          <a href="https://github.com/hertigservices/ascension-data">
            Explore the preserved data ↗
          </a>
          <nav className="community" aria-labelledby="community-title">
            <p className="eyebrow">BUILT BY THE COMMUNITY</p>
            <h2 id="community-title">Follow the restoration</h2>
            <a
              className="community-link"
              href="https://www.reddit.com/r/WoWPrivateServers/comments/1w85j1r/ascension_coa_on_azerothcore_all_21_classes/"
            >
              <MessageCircle size={20} aria-hidden="true" />
              <span>
                Jealous-Sound-3067’s Reddit post
                <small>All 21 classes, transmogrification, and more</small>
              </span>
              <ExternalLink size={15} aria-hidden="true" />
            </a>
          </nav>
        </aside>
      </div>
      <footer>
        Independent community preservation project · Not affiliated with Project
        Ascension
      </footer>
    </main>
  );
}
