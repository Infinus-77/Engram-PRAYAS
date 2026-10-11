/**
 * ENGRAM Comprehension — Client Logic
 *
 * Implements:
 *  1. Linear section locking: Read → Write → Result
 *     - Each tab locked until previous phase is fulfilled
 *     - Completed phases are re-locked (no going back)
 *  2. High-precision SVG ring countdown timer with telemetry thresholds
 *  3. Purge article text from DOM on transition to Write panel
 *  4. "I'm done early" gate (unlocked at 30 seconds)
 *  5. Real-time dynamic word counter scale (<15, 15-29, 30+)
 *  6. Async submission with Jev + LLM analysis loading states
 *  7. Full Bento-grid result rendering matching the Stitch design
 */

// ── Section Lock Helpers ─────────────────────────────────────────
/**
 * Lock a tab so it cannot be navigated to.
 * Adds visual cues (opacity, cursor, lock icon prefix) and sets data-locked.
 */
window.lockTab = function (stateId) {
    const btn = document.getElementById("btn-" + stateId);
    if (!btn) return;
    btn.disabled = true;                          // browser-native: blocks ALL interaction
    btn.dataset.locked = "true";
    btn.setAttribute("aria-disabled", "true");
    btn.classList.add("opacity-40", "cursor-not-allowed", "pointer-events-none");
    btn.classList.remove("cursor-pointer");
    btn.removeAttribute("onclick");
    // Prefix with lock glyph if not already present
    if (!btn.dataset.lockPrefixed) {
        btn.dataset.lockPrefixed = "true";
        const lock = document.createElement("span");
        lock.className = "material-symbols-outlined text-[14px] shrink-0 lock-icon";
        lock.textContent = "lock";
        btn.prepend(lock);
    }
};

/**
 * Unlock a tab so the user can navigate to it.
 */
window.unlockTab = function (stateId) {
    const btn = document.getElementById("btn-" + stateId);
    if (!btn) return;
    btn.disabled = false;                         // re-enable native interaction
    btn.dataset.locked = "false";
    btn.setAttribute("aria-disabled", "false");
    btn.classList.remove("opacity-40", "cursor-not-allowed", "pointer-events-none");
    btn.setAttribute("onclick", `switchState('${stateId}')`);
    // Remove lock icon
    const lockIcon = btn.querySelector(".lock-icon");
    if (lockIcon) lockIcon.remove();
    delete btn.dataset.lockPrefixed;
};

// ── Global State Switcher ─────────────────────────────────────────
window.switchState = function (targetState) {
    // Guard: refuse navigation to a locked tab
    const targetBtn = document.getElementById("btn-" + targetState);
    if (targetBtn && targetBtn.dataset.locked === "true") return;

    const panels = document.querySelectorAll(".assessment-panel");
    panels.forEach((p) => {
        p.classList.add("hidden");
        p.classList.remove("flex");
    });

    const buttons = document.querySelectorAll(".state-tab-btn");
    buttons.forEach((btn) => {
        // Only reset visual active state — do NOT remove locked styles
        btn.classList.remove(
            "bg-secondary-container",
            "text-on-secondary-container",
            "shadow-[3px_3px_0px_#0E1013]"
        );
        if (btn.dataset.locked !== "true") {
            btn.classList.add(
                "bg-surface-container",
                "text-on-surface",
                "shadow-[2px_2px_0px_#0E1013]"
            );
        }
    });

    const targetPanel = document.getElementById("panel-" + targetState);
    if (targetPanel) {
        targetPanel.classList.remove("hidden");
        targetPanel.classList.add("flex");
    }

    if (targetBtn) {
        targetBtn.classList.remove(
            "bg-surface-container",
            "text-on-surface",
            "shadow-[2px_2px_0px_#0E1013]"
        );
        targetBtn.classList.add(
            "bg-secondary-container",
            "text-on-secondary-container",
            "shadow-[3px_3px_0px_#0E1013]"
        );
    }
};

document.addEventListener("DOMContentLoaded", () => {
    const app = document.getElementById("session-app");
    if (!app) return;

    const sessionId = app.dataset.sessionId;
    const readSeconds = parseInt(app.dataset.readSeconds, 10) || 75;
    const hasArticle = app.dataset.hasArticle === "true";
    const hasAttempt = app.dataset.hasAttempt === "true";

    // SVG Ring Gauge constants (radius = 68px)
    const RING_RADIUS = 68;
    const CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS; // ~427.256
    const MIN_READ_SECONDS = 30;

    let timerInterval = null;

    // ── Anti-Copy & Selection Defense for Stimulus ──────────────
    const articleBody = document.getElementById("article-body");
    const articleContainer = document.getElementById("article-body-container");
    [articleBody, articleContainer].forEach((el) => {
        if (!el) return;
        el.addEventListener("copy", (e) => e.preventDefault());
        el.addEventListener("cut", (e) => e.preventDefault());
        el.addEventListener("contextmenu", (e) => e.preventDefault());
        el.addEventListener("selectstart", (e) => e.preventDefault());
        el.addEventListener("dragstart", (e) => e.preventDefault());
    });
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && ["c", "C", "x", "X", "a", "A"].includes(e.key)) {
            const selection = window.getSelection();
            if (articleBody && selection && selection.anchorNode && articleBody.contains(selection.anchorNode)) {
                e.preventDefault();
            }
        }
    });

    // ── Timer & Progression Controller ──────────────────────────
    if (hasArticle && !hasAttempt) {
        let remaining = readSeconds;
        const progressEl = document.getElementById("timer-progress");
        const timeEl = document.getElementById("timer-time");
        const doneBtn = document.getElementById("done-early-btn");
        const hintText = document.getElementById("done-hint-text");

        if (progressEl) {
            progressEl.style.strokeDasharray = CIRCUMFERENCE;
            progressEl.style.strokeDashoffset = 0;
        }

        timerInterval = setInterval(() => {
            remaining--;
            if (timeEl) timeEl.textContent = remaining;

            // Update SVG Ring Gauge
            if (progressEl) {
                const fraction = 1 - remaining / readSeconds;
                progressEl.style.strokeDashoffset = CIRCUMFERENCE * fraction;

                // Color telemetry shifts
                if (remaining <= 10) {
                    progressEl.setAttribute("stroke", "#FF334B"); // Urgent Coral Red
                    if (timeEl) timeEl.style.color = "#ba1a1a";
                } else if (remaining <= 20) {
                    progressEl.setAttribute("stroke", "#FF9F1C"); // Caution Tangerine
                    if (timeEl) timeEl.style.color = "#6f5400";
                } else {
                    progressEl.setAttribute("stroke", "#FFC72C"); // Marigold Amber
                    if (timeEl) timeEl.style.color = "#0E1013";
                }
            }

            // Participant progress gate check
            const elapsed = readSeconds - remaining;
            if (elapsed >= MIN_READ_SECONDS && doneBtn) {
                doneBtn.disabled = false;
                doneBtn.classList.remove(
                    "cursor-not-allowed",
                    "opacity-80",
                    "bg-surface-container-high"
                );
                doneBtn.classList.add(
                    "bg-secondary-container",
                    "text-on-secondary-container",
                    "hover:bg-[#f6bf22]",
                    "shadow-[4px_4px_0px_#0E1013]",
                    "active:translate-x-[2px]",
                    "active:translate-y-[2px]"
                );
                if (hintText) {
                    hintText.textContent = "✓ Minimum time met — progression unlocked";
                }
            } else if (hintText) {
                hintText.textContent = `⏱ Available after 30 seconds (${elapsed}s elapsed)`;
            }

            // Expiry: purge DOM and transition to Write
            if (remaining <= 0) {
                clearInterval(timerInterval);
                purgeArticleAndProceed();
            }
        }, 1000);

        if (doneBtn) {
            doneBtn.addEventListener("click", () => {
                if (readSeconds - remaining >= MIN_READ_SECONDS) {
                    clearInterval(timerInterval);
                    purgeArticleAndProceed();
                }
            });
        }
    }

    function purgeArticleAndProceed() {
        // Immediately wipe article text from the DOM (security)
        const articleContainer = document.getElementById("article-body-container");
        if (articleContainer) articleContainer.remove();
        const articleBodyEl = document.getElementById("article-body");
        if (articleBodyEl) articleBodyEl.remove();

        // Reload the page: the server knows text_served=True + no attempt
        // → it renders Write phase with Read+Result locked natively in HTML.
        // This is the only reliable way to enforce correct lock state.
        window.location.reload();
    }

    // ── Word Counter & Live Validation Scale ────────────────────
    const textarea = document.getElementById("summary-input");
    const wordDisplay = document.getElementById("word-count-val");
    const thresholdBadge = document.getElementById("word-threshold-badge");
    const thresholdText = document.getElementById("word-threshold-text");

    if (textarea && wordDisplay) {
        textarea.addEventListener("input", () => {
            const text = textarea.value.trim();
            const count = text ? text.split(/\s+/).length : 0;
            wordDisplay.textContent = count;

            if (thresholdBadge && thresholdText) {
                if (count < 15) {
                    thresholdBadge.className =
                        "bg-[#FFEAEA] border-[2px] border-[#FF334B] text-[#ba1a1a] px-space-sm py-space-xs font-label-sm text-label-sm font-bold flex items-center gap-space-xs shadow-[2px_2px_0px_#0E1013]";
                    thresholdText.textContent = "< 15 words: Too short to evaluate";
                } else if (count < 30) {
                    thresholdBadge.className =
                        "bg-secondary-fixed text-on-secondary-fixed border-[2px] border-[#0E1013] px-space-sm py-space-xs font-label-sm text-label-sm font-bold flex items-center gap-space-xs shadow-[2px_2px_0px_#0E1013]";
                    thresholdText.textContent = "15–29 words: Developing summary";
                } else {
                    thresholdBadge.className =
                        "bg-[#E8F8EE] border-[2px] border-[#00C853] text-[#004e1c] px-space-sm py-space-xs font-label-sm text-label-sm font-bold flex items-center gap-space-xs shadow-[2px_2px_0px_#0E1013]";
                    thresholdText.textContent = "✓ Threshold Met (>= 30 words)";
                }
            }
        });
    }

    // ── Submit Summary to API Pipeline ──────────────────────────
    window.submitSummary = async function () {
        const summary = textarea ? textarea.value.trim() : "";
        if (!summary) {
            showError("Please write a summary before submitting.");
            return;
        }
        if (summary.split(/\s+/).length < 10) {
            showError("Your summary is too short. Please write at least a few complete sentences.");
            return;
        }

        const submitBtn = document.getElementById("submit-btn");
        const statusInfo = document.getElementById("submit-status-info");
        const loading = document.getElementById("submit-loading");
        const errorEl = document.getElementById("submit-error");

        if (submitBtn) submitBtn.style.display = "none";
        if (statusInfo) statusInfo.style.display = "none";
        if (loading) loading.style.display = "flex";
        if (errorEl) errorEl.style.display = "none";

        try {
            const response = await fetch(`/sessions/${sessionId}/submit`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ summary }),
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || "Submission evaluation failed");
            }

            // Reload: server now has has_attempt=True → renders Result phase
            // with Read+Write locked natively. The SSR result panel shows the
            // full score breakdown — no need for client-side renderDynamicResult.
            await response.json(); // consume body before redirect
            window.location.reload();
        } catch (err) {
            if (loading) loading.style.display = "none";
            if (statusInfo) statusInfo.style.display = "flex";
            if (submitBtn) submitBtn.style.display = "inline-flex";
            showError(err.message);
        }
    };

    function showError(message) {
        const errorEl = document.getElementById("submit-error");
        const errorMsg = document.getElementById("submit-error-msg");
        if (!errorEl || !errorMsg) return;

        errorMsg.innerHTML = `<strong class="font-bold text-[#ba1a1a]">⚠ Validation alert:</strong> ${escapeHtml(message)}`;
        errorEl.style.display = "flex";
        setTimeout(() => {
            errorEl.style.display = "none";
        }, 8000);
    }

    // ── Dynamic Result Renderer (Matching Stitch Bento Grid) ────
    function renderDynamicResult(result) {
        const container = document.getElementById("result-dynamic");
        if (!container) return;

        const rating = result.rating || 5;
        const feedback = result.feedback || "";
        const covered = result.covered || [];
        const missed = result.missed || [];
        const flags = result.flags || [];
        const source = (result.source || "jev").toUpperCase();

        const { qualitativeTitle, badgeColorClass } = getScoreTier(rating);

        let html = `
        <div class="flex flex-col gap-space-lg w-full">
            <!-- Session Result Header -->
            <div class="bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-md flex flex-col md:flex-row items-start md:items-center justify-between gap-space-md">
                <div>
                    <div class="flex items-center gap-space-sm">
                        <span class="bg-tertiary-fixed text-on-tertiary-fixed font-label-sm text-label-sm font-bold px-space-sm py-space-xs border-[2px] border-[#0E1013] shadow-[2px_2px_0px_#0E1013]">
                            EVALUATION COMPLETED • SOURCE: ${escapeHtml(source)}
                        </span>
                        <span class="font-label-sm text-label-sm text-on-surface-variant font-bold">SESSION #${escapeHtml(sessionId.slice(0, 8))}</span>
                    </div>
                    <h2 class="font-headline-md text-headline-md uppercase text-on-surface tracking-tight mt-1 font-bold">AI Comprehension Evaluation Matrix</h2>
                </div>
                <div class="flex flex-wrap items-center gap-space-sm">
                    <a href="/sessions/${escapeHtml(sessionId)}/certificate" target="_blank" download class="bg-primary text-on-primary border-[2px] border-[#0E1013] px-space-md py-space-xs font-label-sm text-label-sm font-bold uppercase shadow-[3px_3px_0px_#0E1013] hover:bg-primary-container hover:text-on-primary hover:shadow-[5px_5px_0px_#0E1013] transition-all flex items-center gap-space-xs">
                        <span class="material-symbols-outlined text-[18px]">workspace_premium</span>
                        <span>DOWNLOAD AUDIT CERTIFICATE (PDF)</span>
                    </a>
                    <a href="/" class="bg-surface-container-lowest border-[2px] border-[#0E1013] px-space-md py-space-xs font-label-sm text-label-sm font-bold uppercase text-on-surface shadow-[3px_3px_0px_#0E1013] hover:bg-surface-container transition-colors flex items-center gap-space-xs">
                        <span class="material-symbols-outlined text-[16px]">arrow_back</span>
                        <span>← Try Another Article</span>
                    </a>
                </div>
            </div>

            <!-- Top Scoring Tier Bento Grid -->
            <div class="grid grid-cols-1 md:grid-cols-12 gap-space-lg">
                <!-- Score Card 1: Massive Geometric Score Card (5 Cols) -->
                <div class="md:col-span-5 bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-lg flex flex-col justify-between">
                    <div class="flex items-center justify-between border-b-[2px] border-[#0E1013] pb-space-sm">
                        <span class="font-label-sm text-label-sm font-bold uppercase text-on-surface">Comprehension Index</span>
                        <span class="bg-primary text-on-primary font-label-sm text-label-sm font-bold px-space-sm py-0.5 border border-[#0E1013]">CALCULATED</span>
                    </div>
                    <div class="py-space-md flex items-baseline gap-space-sm">
                        <span class="font-display text-display font-extrabold text-on-surface tracking-tighter leading-none">${rating}</span>
                        <span class="font-headline-sm text-headline-sm font-bold text-on-surface-variant uppercase">/ 10</span>
                    </div>
                    <div class="flex flex-col gap-space-sm pt-space-xs border-t-[2px] border-[#0E1013]">
                        <div class="flex items-center justify-between">
                            <span class="font-label-sm text-label-sm font-bold text-on-surface uppercase">Qualitative Rating:</span>
                            <span class="${badgeColorClass} font-label-sm text-label-sm font-bold px-space-md py-space-xs border-[2px] border-[#0E1013] shadow-[2px_2px_0px_#0E1013]">
                                ${qualitativeTitle}
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Score Card 2: AI Evaluator Feedback (7 Cols) -->
                <div class="md:col-span-7 bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] flex flex-col">
                    <div class="bg-secondary-container border-b-[3px] border-[#0E1013] px-space-lg py-space-sm flex items-center justify-between">
                        <div class="flex items-center gap-space-xs font-label-md text-label-md font-bold uppercase text-on-secondary-container">
                            <span class="material-symbols-outlined text-[20px]">lightbulb</span>
                            <span>Hybrid AI Evaluator Feedback</span>
                        </div>
                        <span class="font-label-sm text-label-sm font-bold bg-surface-container-lowest px-space-xs border border-[#0E1013]">LLM-RUBRIC-V4</span>
                    </div>
                    <div class="p-space-lg flex flex-col justify-between h-full gap-space-md">
                        <p class="font-body-md text-body-md text-on-surface leading-relaxed">
                            “${escapeHtml(feedback)}”
                        </p>
                    </div>
                </div>
            </div>

            <!-- Point-by-Point Breakdown: Covered & Missed -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-space-lg">
                <!-- Covered Points Column -->
                <div class="bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-lg flex flex-col gap-space-md">
                    <div class="flex items-center justify-between border-b-[3px] border-[#00C853] pb-space-sm">
                        <span class="font-headline-sm text-headline-sm text-[#006928] uppercase font-bold flex items-center gap-space-xs">
                            <span class="material-symbols-outlined text-[#006928] text-[22px]">check_circle</span>
                            ✓ Covered Points (${covered.length})
                        </span>
                    </div>
                    <div class="flex flex-col gap-space-sm">`;

        if (covered.length > 0) {
            covered.forEach((point) => {
                html += `
                <div class="bg-[#E8F8EE] border-[3px] border-[#0E1013] shadow-[3px_3px_0px_#0E1013] p-space-md font-body-sm text-body-sm text-on-surface">
                    <strong>✓ Concept covered:</strong> ${escapeHtml(point)}
                </div>`;
            });
        } else {
            html += `<p class="font-body-sm text-on-surface-variant italic">No rubric points marked as fully covered.</p>`;
        }

        html += `
                    </div>
                </div>

                <!-- Areas to Improve Column -->
                <div class="bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-lg flex flex-col gap-space-md">
                    <div class="flex items-center justify-between border-b-[3px] border-[#FF334B] pb-space-sm">
                        <span class="font-headline-sm text-headline-sm text-[#ba1a1a] uppercase font-bold flex items-center gap-space-xs">
                            <span class="material-symbols-outlined text-[#ba1a1a] text-[22px]">cancel</span>
                            ✗ Areas to Improve (${missed.length})
                        </span>
                    </div>
                    <div class="flex flex-col gap-space-sm">`;

        if (missed.length > 0) {
            missed.forEach((point) => {
                html += `
                <div class="bg-[#FFEAEA] border-[3px] border-[#0E1013] shadow-[3px_3px_0px_#0E1013] p-space-md font-body-sm text-body-sm text-on-surface">
                    <strong>✗ Missed or incomplete:</strong> ${escapeHtml(point)}
                </div>`;
            });
        } else {
            html += `
            <div class="bg-[#E8F8EE] border-[2px] border-[#00C853] p-space-md font-body-sm font-bold text-[#006928]">
                ✓ Excellent! No major conceptual points were omitted.
            </div>`;
        }

        html += `
                    </div>
                </div>
            </div>`;

        // Feature 5: Socratic Follow-Up Interrogation (Dynamic)
        if (missed.length > 0) {
            html += `
            <div class="bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-lg flex flex-col gap-space-md">
                <div class="flex flex-col md:flex-row md:items-center justify-between border-b-[3px] border-[#0E1013] pb-space-sm gap-space-sm">
                    <div class="flex items-center gap-space-sm">
                        <span class="inline-flex items-center justify-center w-8 h-8 bg-secondary-container text-on-secondary-container border-[2px] border-[#0E1013] font-bold">
                            <span class="material-symbols-outlined text-[20px]">psychology_alt</span>
                        </span>
                        <div>
                            <h3 class="font-headline-sm text-headline-sm uppercase font-bold text-on-surface">SOCRATIC RETRIEVAL INTERROGATION</h3>
                            <p class="font-label-sm text-label-sm text-on-surface-variant font-medium">Targeting ${missed.length} missed concepts to reinforce retrieval pathways</p>
                        </div>
                    </div>
                    <button type="button" id="btn-load-socratic" onclick="loadSocraticQuestions('${escapeHtml(sessionId)}')" class="bg-secondary-container text-on-secondary-container border-[2px] border-[#0E1013] px-space-md py-space-xs font-label-sm text-label-sm font-bold uppercase shadow-[2px_2px_0px_#0E1013] hover:shadow-[4px_4px_0px_#0E1013] transition-all flex items-center gap-space-xs self-start md:self-auto">
                        <span class="material-symbols-outlined text-[18px]">auto_awesome</span>
                        <span>START SOCRATIC DRILL</span>
                    </button>
                </div>
                <div id="socratic-content-${escapeHtml(sessionId)}" class="flex flex-col gap-space-md">
                    <p class="font-body-sm text-body-sm text-on-surface-variant">Click <strong>Start Socratic Drill</strong> above to generate targeted comprehension questions focused strictly on the concepts you missed.</p>
                </div>
            </div>`;
        }

        // System Flags Bar
        if (flags.length > 0) {
            html += `
            <div class="bg-surface-container-lowest border-[3px] border-[#0E1013] shadow-[6px_6px_0px_#0E1013] p-space-md flex flex-col gap-space-xs">
                <span class="font-label-sm text-label-sm font-bold uppercase text-on-surface">Automated Proctoring & Quality Audit Flags:</span>
                <div class="flex flex-wrap items-center gap-space-sm">`;
            flags.forEach((flag) => {
                html += `
                <span class="bg-secondary-container border-[2px] border-[#0E1013] px-space-sm py-space-xs font-label-sm text-label-sm font-bold text-on-secondary-container shadow-[2px_2px_0px_#0E1013]">
                    ⚠ ${escapeHtml(flag.replace(/_/g, " ").toUpperCase())}
                </span>`;
            });
            html += `
                </div>
            </div>`;
        }

        html += `</div>`;
        container.innerHTML = html;
    }

    function getScoreTier(rating) {
        if (rating >= 9) {
            return {
                qualitativeTitle: "OUTSTANDING",
                badgeColorClass: "bg-[#00C853] text-[#ffffff]",
            };
        }
        if (rating >= 7) {
            return {
                qualitativeTitle: "GOOD",
                badgeColorClass: "bg-primary-container text-on-primary",
            };
        }
        if (rating >= 5) {
            return {
                qualitativeTitle: "AVERAGE",
                badgeColorClass: "bg-secondary-container text-on-secondary-container",
            };
        }
        if (rating >= 3) {
            return {
                qualitativeTitle: "BELOW AVERAGE",
                badgeColorClass: "bg-[#FF9F1C] text-[#0E1013]",
            };
        }
        return {
            qualitativeTitle: "NEEDS IMPROVEMENT",
            badgeColorClass: "bg-error text-on-error",
        };
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
    window.escapeHtml = escapeHtml;
});

// ── Feature 5: Socratic Follow-Up Interrogation Global Handlers ──
window.loadSocraticQuestions = async function(sessionId) {
    const container = document.getElementById(`socratic-content-${sessionId}`) || document.querySelector('[id^="socratic-content-"]');
    const btn = document.getElementById("btn-load-socratic");
    if (!container) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="inline-block animate-spin material-symbols-outlined text-[18px]">progress_activity</span> <span>SYNTHESIZING QUESTIONS...</span>`;
    }

    try {
        const resp = await fetch(`/sessions/${encodeURIComponent(sessionId)}/followup`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Failed to generate questions (${resp.status})`);
        }
        const data = await resp.json();
        const questions = data.questions || [];

        if (questions.length === 0) {
            container.innerHTML = `
                <div class="bg-[#E8F8EE] border-[2px] border-[#00C853] p-space-md font-body-sm font-bold text-[#006928] flex items-center gap-space-sm">
                    <span class="material-symbols-outlined text-[20px]">verified</span>
                    <span>All rubric key points were successfully covered. Socratic reinforcement not required!</span>
                </div>`;
            if (btn) btn.style.display = "none";
            return;
        }

        let qHtml = `<div class="flex flex-col gap-space-md">`;
        questions.forEach((q, idx) => {
            const escapedQ = window.escapeHtml(q.question);
            const escapedHint = window.escapeHtml(q.hint || "");
            const escapedConcept = window.escapeHtml(q.target_concept || "");
            qHtml += `
            <div id="socratic-card-${idx}" class="bg-surface-container border-[2px] border-[#0E1013] p-space-md flex flex-col gap-space-sm shadow-[3px_3px_0px_#0E1013]">
                <div class="flex flex-wrap items-center justify-between gap-space-xs">
                    <span class="bg-primary text-on-primary font-label-sm text-label-sm font-bold px-space-xs py-0.5 border border-[#0E1013]">
                        PROMPT ${idx + 1} OF ${questions.length}
                    </span>
                    <span class="bg-secondary-container text-on-secondary-container font-label-sm text-label-sm font-bold px-space-xs py-0.5 border border-[#0E1013]">
                        MISSED CONCEPT: ${escapedConcept}
                    </span>
                </div>

                <p class="font-body-md text-body-md font-bold text-on-surface leading-snug">
                    ${escapedQ}
                </p>

                ${escapedHint ? `
                <details class="cursor-pointer font-label-sm text-label-sm text-on-surface-variant">
                    <summary class="font-bold text-primary flex items-center gap-1 hover:underline">
                        <span class="material-symbols-outlined text-[16px]">lightbulb</span> Reveal Socratic Guiding Hint
                    </summary>
                    <p class="mt-1 p-space-xs bg-surface-container-lowest border border-[#0E1013] text-on-surface italic">
                        ${escapedHint}
                    </p>
                </details>` : ''}

                <div class="mt-space-xs flex flex-col gap-space-xs">
                    <textarea id="socratic-input-${idx}" rows="2" class="w-full bg-surface-container-lowest border-[2px] border-[#0E1013] p-space-sm font-body-sm text-body-sm text-on-surface focus:outline-none focus:shadow-[3px_3px_0px_#2A52BE] transition-shadow resize-y" placeholder="Demonstrate your comprehension of this concept in your own words..."></textarea>
                    
                    <div class="flex items-center justify-between pt-space-xs">
                        <button type="button" onclick="submitFollowupAnswer('${sessionId}', ${idx})" id="socratic-btn-${idx}" class="bg-primary text-on-primary border-[2px] border-[#0E1013] px-space-md py-space-xs font-label-sm text-label-sm font-bold uppercase shadow-[2px_2px_0px_#0E1013] hover:shadow-[4px_4px_0px_#0E1013] transition-all flex items-center gap-space-xs">
                            <span class="material-symbols-outlined text-[16px]">check_circle</span>
                            <span>SUBMIT FOR SOCRATIC ASSESSMENT</span>
                        </button>
                    </div>

                    <div id="socratic-result-${idx}" class="hidden mt-space-xs"></div>
                </div>
            </div>`;
        });
        qHtml += `</div>`;
        container.innerHTML = qHtml;

        // Store active questions array for answer submission lookups
        window._activeSocraticQuestions = questions;
        if (btn) btn.style.display = "none";

    } catch (err) {
        container.innerHTML = `
            <div class="bg-[#FFEAEA] border-[2px] border-error p-space-sm font-label-sm text-label-sm text-error font-bold flex items-center gap-space-xs">
                <span class="material-symbols-outlined text-[18px]">error</span>
                <span>Could not load Socratic questions: ${window.escapeHtml(err.message)}</span>
            </div>`;
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<span class="material-symbols-outlined text-[18px]">refresh</span> <span>RETRY SOCRATIC DRILL</span>`;
        }
    }
};

window.submitFollowupAnswer = async function(sessionId, idx) {
    const input = document.getElementById(`socratic-input-${idx}`);
    const btn = document.getElementById(`socratic-btn-${idx}`);
    const resultBox = document.getElementById(`socratic-result-${idx}`);
    const answer = input ? input.value.trim() : "";

    if (!answer) {
        alert("Please write your answer before submitting.");
        return;
    }

    const questionObj = (window._activeSocraticQuestions && window._activeSocraticQuestions[idx]) || {};
    const questionText = questionObj.question || "";
    const targetConcept = questionObj.target_concept || "";

    btn.disabled = true;
    btn.innerHTML = `<span class="inline-block animate-spin material-symbols-outlined text-[16px]">progress_activity</span> <span>EVALUATING RETRIEVAL...</span>`;

    try {
        const resp = await fetch(`/sessions/${encodeURIComponent(sessionId)}/followup/answer`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                question: questionText,
                target_concept: targetConcept,
                answer: answer
            })
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Evaluation failed (${resp.status})`);
        }

        const data = await resp.json();
        const score = data.score || 1;
        const correct = data.correct || false;
        const explanation = data.explanation || "";

        const borderCol = correct ? "border-[#00C853] bg-[#E8F8EE]" : "border-[#FF334B] bg-[#FFEAEA]";
        const badgeCol = correct ? "bg-[#00C853] text-[#ffffff]" : "bg-error text-on-error";
        const statusText = correct ? "CONCEPT CONSOLIDATED ✓" : "RECALL INCOMPLETE ✗";

        resultBox.className = `border-[2px] ${borderCol} p-space-md flex flex-col gap-space-xs shadow-[2px_2px_0px_#0E1013]`;
        resultBox.innerHTML = `
            <div class="flex items-center justify-between">
                <span class="${badgeCol} font-label-sm text-label-sm font-bold px-space-xs py-0.5 border border-[#0E1013]">
                    SCORE: ${score} / 5 • ${statusText}
                </span>
            </div>
            <p class="font-body-sm text-body-sm text-on-surface mt-1">
                <strong>Pedagogical Feedback:</strong> ${window.escapeHtml(explanation)}
            </p>
        `;
        resultBox.classList.remove("hidden");
        btn.style.display = "none";
        if (input) input.disabled = true;

    } catch (err) {
        btn.disabled = false;
        btn.innerHTML = `<span class="material-symbols-outlined text-[16px]">check_circle</span> <span>SUBMIT FOR SOCRATIC ASSESSMENT</span>`;
        alert(`Evaluation error: ${err.message}`);
    }
};
