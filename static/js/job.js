document.addEventListener('DOMContentLoaded', () => {
    const body = document.body;
    const jobId = body.dataset.jobId;
    let initialStatus = body.dataset.jobStatus;

    // Elements
    const progressCard = document.getElementById('progress-card');
    const progressBar = document.getElementById('progress-bar');
    const progressMsg = document.getElementById('progress-message');
    const progressPct = document.getElementById('progress-pct');
    const headerStatus = document.getElementById('job-header-status');
    
    const resultsCard = document.getElementById('results-card');
    const langButtonsContainer = document.getElementById('lang-buttons');
    const timedContent = document.getElementById('timed-text-content');
    const srtContent = document.getElementById('srt-text-content');
    const plainContent = document.getElementById('plain-text-content');
    
    const btnDlSrt = document.getElementById('btn-dl-srt');
    const btnDlVtt = document.getElementById('btn-dl-vtt');
    const btnDlTxt = document.getElementById('btn-dl-txt');
    const btnDlMp3 = document.getElementById('btn-dl-mp3');
    const btnDlVideo = document.getElementById('btn-dl-video');
    const btnDlDiscourse = document.getElementById('btn-dl-discourse');
    const discourseContent = document.getElementById('discourse-text-content');
    const btnCopy = document.getElementById('btn-copy-text');
    const btnDlZip = document.getElementById('btn-download-zip');
    
    const videoContainer = document.getElementById('video-player-container');
    const audioContainer = document.getElementById('audio-player-container');
    const retentionNotice = document.getElementById('media-retention-notice');
    const deletedNotice = document.getElementById('media-deleted-notice');
    const videoSkippedNotice = document.getElementById('video-skipped-notice');
    const mediaPlayer = document.getElementById('media-player');
    const audioPlayer = document.getElementById('audio-player');
    const metaMediaStatus = document.getElementById('meta-media-status');

    let currentJobData = null;
    let activeLanguage = null;

    // Try reading initial embedded JSON
    try {
        const jsonEl = document.getElementById('job-data-json');
        if (jsonEl && jsonEl.textContent) {
            currentJobData = JSON.parse(jsonEl.textContent);
        }
    } catch (e) {
        console.error("Erro ao carregar dados iniciais do job:", e);
    }

    function renderResults(job) {
        if (!job.results || Object.keys(job.results).length === 0) return;
        const jId = job.id || jobId;
        
        if (progressCard) progressCard.classList.add('hidden');
        if (resultsCard) resultsCard.classList.remove('hidden');
        if (headerStatus) {
            headerStatus.innerHTML = '<span class="status-badge status-done">Concluído</span>';
        }

        // ZIP download link
        if (btnDlZip && job.zip_file) {
            btnDlZip.href = `/api/jobs/${jId}/download/${job.zip_file}`;
        }

        // MP3 download button
        if (btnDlMp3) {
            if (job.mp3_file) {
                btnDlMp3.href = `/api/jobs/${jId}/download/${job.mp3_file}`;
                btnDlMp3.classList.remove('hidden');
            } else {
                btnDlMp3.classList.add('hidden');
            }
        }

        // Video download button (Hardsub .MP4)
        if (btnDlVideo) {
            const hasVideo = (job.video_file && job.video_exists !== false && !job.media_deleted);
            if (hasVideo) {
                btnDlVideo.href = `/api/jobs/${jId}/download/${job.video_file}`;
                btnDlVideo.classList.remove('hidden');
            } else {
                btnDlVideo.classList.add('hidden');
            }
        }

        // Video skipped notice
        if (videoSkippedNotice) {
            if (job.video_skipped_reason) {
                videoSkippedNotice.textContent = `ℹ️ ${job.video_skipped_reason}`;
                videoSkippedNotice.classList.remove('hidden');
            } else {
                videoSkippedNotice.classList.add('hidden');
            }
        }

        // Media and notices visibility
        const hasMedia = (job.media_exists !== false && !job.media_deleted);
        if (hasMedia) {
            if (videoContainer) videoContainer.classList.remove('hidden');
            if (retentionNotice) retentionNotice.classList.remove('hidden');
            if (audioContainer) audioContainer.classList.add('hidden');
            if (deletedNotice) deletedNotice.classList.add('hidden');
            if (metaMediaStatus) {
                metaMediaStatus.innerHTML = '<span class="status-badge status-proc">Ativa (60 min)</span>';
            }
            if (mediaPlayer) {
                const srcEl = mediaPlayer.querySelector('source');
                if (srcEl && !srcEl.src.includes(`/api/jobs/${jId}/media`)) {
                    srcEl.src = `/api/jobs/${jId}/media`;
                }
                mediaPlayer.load();
            }
        } else {
            if (videoContainer) videoContainer.classList.add('hidden');
            if (retentionNotice) retentionNotice.classList.add('hidden');
            if (deletedNotice) deletedNotice.classList.remove('hidden');
            if (metaMediaStatus) {
                metaMediaStatus.innerHTML = '<span class="status-badge status-done">Liberada (60 min)</span>';
            }
            if (job.mp3_file && audioContainer) {
                audioContainer.classList.remove('hidden');
                if (audioPlayer) {
                    const srcEl = audioPlayer.querySelector('source');
                    if (srcEl) srcEl.src = `/api/jobs/${jId}/download/${job.mp3_file}`;
                    audioPlayer.load();
                }
            } else if (audioContainer) {
                audioContainer.classList.add('hidden');
            }
        }

        // Render language tabs
        if (langButtonsContainer) {
            langButtonsContainer.innerHTML = '';
            const langs = Object.keys(job.results || {});
            const langGroup = document.getElementById('lang-selector-group');
            const viewTabs = document.querySelector('.view-tabs-group');

            if (langs.length === 0) {
                if (langGroup) langGroup.classList.add('hidden');
                if (viewTabs) viewTabs.classList.add('hidden');
                if (btnCopy) btnCopy.classList.add('hidden');
                if (btnDlSrt) btnDlSrt.classList.add('hidden');
                if (btnDlVtt) btnDlVtt.classList.add('hidden');
                if (btnDlTxt) btnDlTxt.classList.add('hidden');
                if (btnDlDiscourse) btnDlDiscourse.classList.add('hidden');
                if (timedContent) {
                    timedContent.textContent = "Processamento concluído: Esta tarefa foi configurada para extração exclusiva de áudio MP3 (sem geração de transcrição textual).\n\nUtilize o botão 'Baixar .MP3' acima para baixar ou o reprodutor para ouvir.";
                }
            } else {
                if (langGroup) langGroup.classList.remove('hidden');
                if (viewTabs) viewTabs.classList.remove('hidden');
                if (btnCopy) btnCopy.classList.remove('hidden');

                langs.forEach((langCode, idx) => {
                    const res = job.results[langCode];
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = `tab-btn lang-tab-btn ${idx === 0 ? 'active' : ''}`;
                    btn.dataset.lang = langCode;
                    btn.textContent = `🌐 ${res.name || langCode}`;
                    
                    btn.addEventListener('click', () => {
                        document.querySelectorAll('.lang-tab-btn').forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');
                        selectLanguage(langCode, job);
                    });
                    
                    langButtonsContainer.appendChild(btn);
                });

                // Set up player tracks
                setupPlayerTracks(job);

                // Select first language
                if (langs.length > 0) {
                    selectLanguage(langs[0], job);
                }
            }
        }
    }

    function setupPlayerTracks(job) {
        if (!mediaPlayer) return;
        const jId = job.id || jobId;
        const tracks = mediaPlayer.querySelectorAll('track');
        tracks.forEach(t => t.remove());

        if (job.results) {
            Object.keys(job.results).forEach((langCode, idx) => {
                const res = job.results[langCode];
                if (res.files && res.files.vtt) {
                    const track = document.createElement('track');
                    track.kind = 'subtitles';
                    track.label = res.name || langCode;
                    track.srclang = langCode;
                    track.src = `/api/jobs/${jId}/download/${res.files.vtt}`;
                    if (idx === 0) track.default = true;
                    mediaPlayer.appendChild(track);
                }
            });
        }
    }

    function formatShortTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) {
            return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function formatSrtTimestamp(seconds) {
        const ms = Math.round((seconds - Math.floor(seconds)) * 1000);
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
    }

    function selectLanguage(langCode, job) {
        activeLanguage = langCode;
        const res = job.results[langCode];
        if (!res) return;
        const jId = job.id || jobId;

        // Update downloads and view tabs conditionally based on res.files
        if (res.files) {
            if (res.files.srt && btnDlSrt) {
                btnDlSrt.href = `/api/jobs/${jId}/download/${res.files.srt}`;
                btnDlSrt.classList.remove('hidden');
            } else if (btnDlSrt) {
                btnDlSrt.classList.add('hidden');
            }

            if (res.files.vtt && btnDlVtt) {
                btnDlVtt.href = `/api/jobs/${jId}/download/${res.files.vtt}`;
                btnDlVtt.classList.remove('hidden');
            } else if (btnDlVtt) {
                btnDlVtt.classList.add('hidden');
            }

            if (res.files.timed_txt && btnDlTxt) {
                btnDlTxt.href = `/api/jobs/${jId}/download/${res.files.timed_txt}`;
                btnDlTxt.classList.remove('hidden');
            } else if (btnDlTxt) {
                btnDlTxt.classList.add('hidden');
            }

            if (res.files.discourse_txt && btnDlDiscourse) {
                btnDlDiscourse.href = `/api/jobs/${jId}/download/${res.files.discourse_txt}`;
                btnDlDiscourse.classList.remove('hidden');
            } else if (btnDlDiscourse) {
                btnDlDiscourse.classList.add('hidden');
            }

            // Adjust tabs visibility
            const tabTimed = document.querySelector('[data-view="timed-view"]');
            const tabSrt = document.querySelector('[data-view="srt-view"]');
            const tabPlain = document.querySelector('[data-view="plain-view"]');
            const tabDiscourse = document.querySelector('[data-view="discourse-view"]');

            if (tabTimed) {
                if (res.files.timed_txt) tabTimed.classList.remove('hidden');
                else tabTimed.classList.add('hidden');
            }
            if (tabSrt) {
                if (res.files.srt) tabSrt.classList.remove('hidden');
                else tabSrt.classList.add('hidden');
            }
            if (tabPlain) {
                if (res.files.plain_txt) tabPlain.classList.remove('hidden');
                else tabPlain.classList.add('hidden');
            }
            if (tabDiscourse) {
                if (res.files.discourse_txt) tabDiscourse.classList.remove('hidden');
                else tabDiscourse.classList.add('hidden');
            }

            // If active tab was hidden, switch to first visible tab
            const activeTab = document.querySelector('.view-tab-btn.active');
            if (activeTab && activeTab.classList.contains('hidden')) {
                const firstVisible = document.querySelector('.view-tab-btn:not(.hidden)');
                if (firstVisible) firstVisible.click();
            }
        }

        // 4. Discourse View
        if (discourseContent) {
            if (res.discourse) {
                discourseContent.textContent = res.discourse;
            } else if (res.files && res.files.discourse_txt) {
                fetch(`/api/jobs/${jId}/download/${res.files.discourse_txt}`)
                    .then(r => r.text())
                    .then(txt => { discourseContent.textContent = txt; })
                    .catch(() => { discourseContent.textContent = 'Carregando discurso...'; });
            } else {
                discourseContent.textContent = 'Formato Discurso não foi gerado para este idioma.';
            }
        }

        // Render views from segments
        const segs = res.segments || [];
        
        // 1. Timed (Minutagem / Ata com Identificação de Locutores: [Pessoa 1], [Pessoa 2])
        let timedStr = '';
        let currentSpeaker = null;
        segs.forEach(s => {
            const speaker = s.speaker || 'Pessoa 1';
            if (speaker !== currentSpeaker) {
                if (timedStr.length > 0) timedStr += '\n';
                timedStr += `[${speaker}]\n`;
                currentSpeaker = speaker;
            }
            timedStr += `[${formatShortTime(s.start)}] ${s.text}\n`;
        });
        if (timedContent) timedContent.textContent = timedStr;

        // 2. SRT
        let srtStr = '';
        segs.forEach((s, idx) => {
            srtStr += `${idx + 1}\n${formatSrtTimestamp(s.start)} --> ${formatSrtTimestamp(s.end)}\n${s.text}\n\n`;
        });
        if (srtContent) srtContent.textContent = srtStr;

        // 3. Plain
        if (plainContent) {
            plainContent.innerHTML = '';
            let para = [];
            segs.forEach(s => {
                para.push(s.text);
                if (/[.!?]$/.test(s.text.trim()) && para.length >= 3) {
                    const p = document.createElement('p');
                    p.textContent = para.join(' ');
                    plainContent.appendChild(p);
                    para = [];
                }
            });
            if (para.length > 0) {
                const p = document.createElement('p');
                p.textContent = para.join(' ');
                plainContent.appendChild(p);
            }
        }
    }

    // View tab switching (Timed vs SRT vs Plain)
    const viewTabBtns = document.querySelectorAll('.view-tab-btn');
    viewTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const viewId = btn.dataset.view;
            if (!viewId) return;

            viewTabBtns.forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.view-content').forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const targetView = document.getElementById(viewId);
            if (targetView) targetView.classList.add('active');
        });
    });

    // Copy to clipboard
    if (btnCopy) {
        btnCopy.addEventListener('click', () => {
            const activeView = document.querySelector('.view-content.active');
            if (!activeView) return;
            const textToCopy = activeView.innerText;
            navigator.clipboard.writeText(textToCopy).then(() => {
                const originalText = btnCopy.textContent;
                btnCopy.textContent = '✅ Copiado!';
                btnCopy.style.borderColor = 'var(--color-primary)';
                btnCopy.style.color = 'var(--color-primary)';
                btnCopy.style.background = 'var(--color-primary-light)';
                setTimeout(() => {
                    btnCopy.textContent = originalText;
                    btnCopy.style.borderColor = '';
                    btnCopy.style.color = '';
                    btnCopy.style.background = '';
                }, 2000);
            });
        });
    }

    // Connect SSE Stream if not completed
    if (initialStatus !== 'completed' && initialStatus !== 'failed') {
        const evtSource = new EventSource(`/api/jobs/${jobId}/stream`);

        evtSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                currentJobData = data;

                if (data.progress !== undefined && progressBar && progressPct) {
                    progressBar.style.width = `${data.progress}%`;
                    progressPct.textContent = `${data.progress}%`;
                }

                if (data.message && progressMsg) {
                    progressMsg.textContent = data.message;
                }

                if (data.status === 'completed') {
                    evtSource.close();
                    const fullJob = Object.assign({}, currentJobData, data, { id: data.id || jobId });
                    renderResults(fullJob);
                } else if (data.status === 'failed') {
                    evtSource.close();
                    if (headerStatus) {
                        headerStatus.innerHTML = '<span class="status-badge status-err">Erro</span>';
                    }
                    if (progressMsg) {
                        progressMsg.textContent = data.message || 'Falha no processamento.';
                        progressMsg.style.color = 'var(--accent-danger)';
                    }
                }
            } catch (err) {
                console.error("Erro ao processar evento SSE:", err);
            }
        };

        evtSource.onerror = () => {
            console.warn("Conexão SSE perdida. Tentando reconectar...");
        };
    } else if (initialStatus === 'completed' && currentJobData) {
        renderResults(currentJobData);
    }
});
