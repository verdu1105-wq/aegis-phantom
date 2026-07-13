import { useState, useEffect, useCallback } from "react";

const API_BASE = process.env.REACT_APP_AEGIS_URL || "https://api.sitrep.media";

const PLATFORMS = [
  { id: "tiktok",    label: "TikTok",     icon: "♪", duration: "30s", color: "#ff0050" },
  { id: "youtube",   label: "YouTube",    icon: "▶", duration: "60s", color: "#ff0000" },
  { id: "substack",  label: "Substack",   icon: "✉", duration: "45s", color: "#ff6719" },
  { id: "instagram", label: "Instagram",  icon: "◈", duration: "30s", color: "#c13584" },
];

const STATUS_META = {
  queued:              { label: "QUEUED",            color: "#888",    pulse: false },
  engineering_prompt:  { label: "ENGINEERING PROMPT",color: "#00bfff", pulse: true  },
  generating_video:    { label: "GENERATING VIDEO",  color: "#ffd700", pulse: true  },
  queued_for_posting:  { label: "READY TO POST",     color: "#00ff88", pulse: false },
  failed:              { label: "FAILED",            color: "#ff4444", pulse: false },
};

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || { label: status.toUpperCase(), color: "#888", pulse: false };
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "3px 10px", borderRadius: 2,
      border: `1px solid ${meta.color}`,
      color: meta.color, fontSize: 10, fontFamily: "'Share Tech Mono', monospace",
      letterSpacing: "0.1em",
    }}>
      {meta.pulse && (
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: meta.color,
          animation: "pulse 1.2s ease-in-out infinite",
          flexShrink: 0,
        }} />
      )}
      {meta.label}
    </span>
  );
}

function JobCard({ job, onRefresh }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{
      background: "#0a0e14",
      border: "1px solid #1a2332",
      borderLeft: `3px solid ${job.status === "queued_for_posting" ? "#00ff88" : job.status === "failed" ? "#ff4444" : "#00bfff"}`,
      marginBottom: 12, padding: "14px 18px",
      fontFamily: "'Share Tech Mono', monospace",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: "#e8f4f8", fontSize: 13, fontWeight: 600, marginBottom: 6, lineHeight: 1.3 }}>
            {job.article_title}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <StatusBadge status={job.status} />
            <span style={{ color: "#445566", fontSize: 10 }}>{job.job_id}</span>
            <span style={{ color: "#445566", fontSize: 10 }}>
              {job.created_at ? new Date(job.created_at).toLocaleString() : ""}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0, alignItems: "center" }}>
          {job.platforms?.map(p => {
            const meta = PLATFORMS.find(x => x.id === p);
            const hasVideo = job.gcs_uris?.[p];
            return (
              <span key={p} style={{
                padding: "2px 8px", borderRadius: 2, fontSize: 10,
                background: hasVideo ? `${meta?.color}22` : "#111",
                border: `1px solid ${hasVideo ? meta?.color : "#223"}`,
                color: hasVideo ? meta?.color : "#445",
              }}>
                {meta?.icon} {meta?.label}
              </span>
            );
          })}
          <button
            onClick={() => setExpanded(e => !e)}
            style={{
              background: "none", border: "1px solid #223", color: "#667",
              padding: "2px 8px", cursor: "pointer", fontSize: 10,
              fontFamily: "'Share Tech Mono', monospace",
            }}
          >
            {expanded ? "COLLAPSE" : "EXPAND"}
          </button>
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: 14, borderTop: "1px solid #1a2332", paddingTop: 14 }}>
          {job.intel_summary && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ color: "#00bfff", fontSize: 10, marginBottom: 4 }}>INTEL SUMMARY</div>
              <div style={{ color: "#aabbcc", fontSize: 12, lineHeight: 1.5 }}>{job.intel_summary}</div>
            </div>
          )}
          {job.key_targets?.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ color: "#00bfff", fontSize: 10, marginBottom: 4 }}>KEY TARGETS</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {job.key_targets.map(t => (
                  <span key={t} style={{
                    padding: "2px 8px", background: "#ff444422",
                    border: "1px solid #ff4444", color: "#ff8888", fontSize: 10, borderRadius: 2,
                  }}>{t}</span>
                ))}
              </div>
            </div>
          )}
          {job.prompts && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ color: "#00bfff", fontSize: 10, marginBottom: 6 }}>GENERATED PROMPTS</div>
              {Object.entries(job.prompts).map(([platform, prompt]) => (
                <div key={platform} style={{ marginBottom: 8 }}>
                  <div style={{ color: "#ffd700", fontSize: 10, marginBottom: 3 }}>
                    {PLATFORMS.find(p => p.id === platform)?.icon} {platform.toUpperCase()}
                  </div>
                  <div style={{
                    background: "#060a0f", border: "1px solid #1a2332",
                    padding: "8px 10px", fontSize: 11, color: "#778899",
                    lineHeight: 1.5, maxHeight: 80, overflowY: "auto",
                  }}>
                    {prompt}
                  </div>
                </div>
              ))}
            </div>
          )}
          {job.gcs_uris && Object.keys(job.gcs_uris).length > 0 && (
            <div>
              <div style={{ color: "#00bfff", fontSize: 10, marginBottom: 6 }}>GCS ASSETS</div>
              {Object.entries(job.gcs_uris).map(([p, uri]) => (
                <div key={p} style={{
                  fontSize: 11, color: "#00ff88",
                  fontFamily: "'Share Tech Mono', monospace", marginBottom: 4,
                }}>
                  {p}: {uri}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ArticleForm({ onSubmit, loading }) {
  const [title, setTitle]       = useState("");
  const [body, setBody]         = useState("");
  const [url, setUrl]           = useState("");
  const [platforms, setPlatforms] = useState(["tiktok", "youtube"]);
  const [priority, setPriority] = useState("normal");

  const togglePlatform = (id) => {
    setPlatforms(prev =>
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
    );
  };

  const handleSubmit = () => {
    if (!title.trim() || !body.trim()) return;
    onSubmit({ article_title: title, article_body: body, article_url: url, platforms, priority });
  };

  return (
    <div style={{
      background: "#0a0e14",
      border: "1px solid #1a2332",
      borderTop: "2px solid #00bfff",
      padding: 20, marginBottom: 24,
    }}>
      <div style={{
        color: "#00bfff", fontSize: 11, letterSpacing: "0.15em",
        fontFamily: "'Share Tech Mono', monospace", marginBottom: 16,
      }}>
        ◈ NEW VIDEO INTEL JOB
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={{ color: "#667788", fontSize: 10, fontFamily: "'Share Tech Mono', monospace", display: "block", marginBottom: 4 }}>
          ARTICLE TITLE
        </label>
        <input
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="US Strikes Iranian Radar Sites After Drone Attack..."
          style={{
            width: "100%", background: "#060a0f", border: "1px solid #1a2332",
            color: "#e8f4f8", padding: "8px 12px", fontSize: 13,
            fontFamily: "'Share Tech Mono', monospace", outline: "none", boxSizing: "border-box",
          }}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={{ color: "#667788", fontSize: 10, fontFamily: "'Share Tech Mono', monospace", display: "block", marginBottom: 4 }}>
          ARTICLE BODY
        </label>
        <textarea
          value={body}
          onChange={e => setBody(e.target.value)}
          rows={6}
          placeholder="Paste the full article text here. AEGIS will extract the intel and engineer the video prompt..."
          style={{
            width: "100%", background: "#060a0f", border: "1px solid #1a2332",
            color: "#aabbcc", padding: "8px 12px", fontSize: 12, resize: "vertical",
            fontFamily: "'Share Tech Mono', monospace", outline: "none", boxSizing: "border-box",
            lineHeight: 1.5,
          }}
        />
      </div>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <label style={{ color: "#667788", fontSize: 10, fontFamily: "'Share Tech Mono', monospace", display: "block", marginBottom: 4 }}>
            ARTICLE URL (optional)
          </label>
          <input
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://sitrep.media/article/..."
            style={{
              width: "100%", background: "#060a0f", border: "1px solid #1a2332",
              color: "#e8f4f8", padding: "8px 12px", fontSize: 12,
              fontFamily: "'Share Tech Mono', monospace", outline: "none", boxSizing: "border-box",
            }}
          />
        </div>
        <div>
          <label style={{ color: "#667788", fontSize: 10, fontFamily: "'Share Tech Mono', monospace", display: "block", marginBottom: 4 }}>
            PRIORITY
          </label>
          <select
            value={priority}
            onChange={e => setPriority(e.target.value)}
            style={{
              background: "#060a0f", border: "1px solid #1a2332",
              color: "#e8f4f8", padding: "8px 12px", fontSize: 12,
              fontFamily: "'Share Tech Mono', monospace", outline: "none", height: 36,
            }}
          >
            <option value="normal">NORMAL</option>
            <option value="urgent">URGENT</option>
            <option value="breaking">BREAKING</option>
          </select>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <label style={{ color: "#667788", fontSize: 10, fontFamily: "'Share Tech Mono', monospace", display: "block", marginBottom: 8 }}>
          TARGET PLATFORMS
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {PLATFORMS.map(p => (
            <button
              key={p.id}
              onClick={() => togglePlatform(p.id)}
              style={{
                padding: "6px 14px", cursor: "pointer",
                background: platforms.includes(p.id) ? `${p.color}22` : "#060a0f",
                border: `1px solid ${platforms.includes(p.id) ? p.color : "#1a2332"}`,
                color: platforms.includes(p.id) ? p.color : "#445566",
                fontSize: 11, fontFamily: "'Share Tech Mono', monospace",
                transition: "all 0.15s",
              }}
            >
              {p.icon} {p.label.toUpperCase()} · {p.duration}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={handleSubmit}
        disabled={loading || !title.trim() || !body.trim() || platforms.length === 0}
        style={{
          padding: "10px 24px",
          background: loading ? "#0a1a2a" : "#003a5c",
          border: `1px solid ${loading ? "#1a2332" : "#00bfff"}`,
          color: loading ? "#445566" : "#00bfff",
          cursor: loading ? "not-allowed" : "pointer",
          fontSize: 12, fontFamily: "'Share Tech Mono', monospace",
          letterSpacing: "0.1em", transition: "all 0.15s",
        }}
      >
        {loading ? "⟳ GENERATING..." : "▶ GENERATE VIDEO INTEL"}
      </button>
    </div>
  );
}

export default function VideoIntelConsole() {
  const [jobs, setJobs]       = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [tab, setTab]         = useState("jobs");   // jobs | queue

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/video/jobs`);
      if (r.ok) setJobs(await r.json());
    } catch (e) {
      console.error("fetchJobs:", e);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 15000);   // poll every 15s
    return () => clearInterval(interval);
  }, [fetchJobs]);

  const handleSubmit = async (payload) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/api/video/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`API ${r.status}`);
      await fetchJobs();
      setTab("jobs");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const activeJobs = jobs.filter(j =>
    ["queued", "engineering_prompt", "generating_video"].includes(j.status)
  );

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Bebas+Neue&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; } 
        ::-webkit-scrollbar-track { background: #060a0f; }
        ::-webkit-scrollbar-thumb { background: #1a2332; }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        @keyframes scanline {
          0% { transform: translateY(-100%); }
          100% { transform: translateY(100vh); }
        }
      `}</style>

      <div style={{
        minHeight: "100vh",
        background: "#060a0f",
        color: "#e8f4f8",
        fontFamily: "'Share Tech Mono', monospace",
        padding: "0 0 60px 0",
        position: "relative",
        overflow: "hidden",
      }}>
        {/* scanline effect */}
        <div style={{
          position: "fixed", top: 0, left: 0, right: 0, height: "2px",
          background: "linear-gradient(transparent, #00bfff22, transparent)",
          animation: "scanline 8s linear infinite",
          pointerEvents: "none", zIndex: 1,
        }} />

        {/* header */}
        <div style={{
          borderBottom: "1px solid #1a2332",
          background: "#060a0f",
          padding: "16px 24px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          position: "sticky", top: 0, zIndex: 10,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{
              fontFamily: "'Bebas Neue', sans-serif",
              fontSize: 22, letterSpacing: "0.1em", color: "#00bfff",
            }}>
              SITREP MEDIA
            </div>
            <div style={{
              borderLeft: "1px solid #1a2332", paddingLeft: 16,
              fontSize: 10, color: "#445566", letterSpacing: "0.15em",
            }}>
              VIDEO INTEL PIPELINE
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {activeJobs.length > 0 && (
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                color: "#ffd700", fontSize: 10,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: "50%",
                  background: "#ffd700",
                  animation: "pulse 1.2s ease-in-out infinite",
                }} />
                {activeJobs.length} JOB{activeJobs.length > 1 ? "S" : ""} RUNNING
              </div>
            )}
            <div style={{ color: "#223344", fontSize: 10 }}>
              AEGIS v22 · CYBERGRID
            </div>
          </div>
        </div>

        <div style={{ maxWidth: 960, margin: "0 auto", padding: "24px 24px 0" }}>

          {/* tab nav */}
          <div style={{ display: "flex", gap: 0, marginBottom: 24, borderBottom: "1px solid #1a2332" }}>
            {[
              { id: "new",   label: "◈ NEW JOB" },
              { id: "jobs",  label: `▶ JOBS (${jobs.length})` },
            ].map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                style={{
                  padding: "10px 20px", cursor: "pointer",
                  background: "none",
                  borderBottom: `2px solid ${tab === t.id ? "#00bfff" : "transparent"}`,
                  border: "none",
                  color: tab === t.id ? "#00bfff" : "#445566",
                  fontSize: 11, fontFamily: "'Share Tech Mono', monospace",
                  letterSpacing: "0.1em", transition: "all 0.15s",
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {error && (
            <div style={{
              background: "#1a0000", border: "1px solid #ff4444",
              color: "#ff8888", padding: "10px 14px", marginBottom: 16, fontSize: 12,
            }}>
              ⚠ ERROR: {error}
            </div>
          )}

          {tab === "new" && (
            <ArticleForm onSubmit={handleSubmit} loading={loading} />
          )}

          {tab === "jobs" && (
            <div>
              <div style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                marginBottom: 16,
              }}>
                <div style={{ color: "#445566", fontSize: 10 }}>
                  {jobs.length} TOTAL · {activeJobs.length} ACTIVE · AUTO-REFRESH 15s
                </div>
                <button
                  onClick={fetchJobs}
                  style={{
                    background: "none", border: "1px solid #1a2332",
                    color: "#445566", padding: "4px 12px", cursor: "pointer",
                    fontSize: 10, fontFamily: "'Share Tech Mono', monospace",
                  }}
                >
                  ↻ REFRESH
                </button>
              </div>
              {jobs.length === 0 ? (
                <div style={{
                  color: "#223344", fontSize: 12, textAlign: "center",
                  padding: "40px 0", border: "1px solid #1a2332",
                }}>
                  NO VIDEO JOBS YET — CREATE ONE IN THE NEW JOB TAB
                </div>
              ) : (
                jobs.map(job => (
                  <JobCard key={job.job_id} job={job} onRefresh={fetchJobs} />
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
