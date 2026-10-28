document.addEventListener('DOMContentLoaded', () => {
    // Tab switching (URL vs File)
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;
            if (!tabId) return;
            
            tabBtns.forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            btn.classList.add('active');
            const targetContent = document.getElementById(tabId);
            if (targetContent) targetContent.classList.add('active');
        });
    });

    // File Dropzone
    const fileInput = document.getElementById('media_file');
    const fileNameDisplay = document.getElementById('file-name-display');
    const dropzone = document.getElementById('dropzone');

    if (fileInput && dropzone) {
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) {
                const file = fileInput.files[0];
                const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
                fileNameDisplay.innerHTML = `<strong>Arquivo selecionado:</strong> ${file.name} (${sizeMb} MB)`;
            }
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropzone.style.borderColor = 'var(--color-primary)';
                dropzone.style.background = 'rgba(30, 77, 43, 0.05)';
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropzone.style.borderColor = 'var(--color-brand-border)';
                dropzone.style.background = '#FAF9F6';
            });
        });

        dropzone.addEventListener('drop', (e) => {
            if (e.dataTransfer.files.length > 0) {
                fileInput.files = e.dataTransfer.files;
                const file = fileInput.files[0];
                const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
                fileNameDisplay.innerHTML = `<strong>Arquivo selecionado:</strong> ${file.name} (${sizeMb} MB)`;
            }
        });
    }

    // Form Submission
    const form = document.getElementById('transcribe-form');
    const submitBtn = document.getElementById('submit-btn');

    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const formData = new FormData(form);
            const activeTab = document.querySelector('.tab-btn.active').dataset.tab;
            
            if (activeTab === 'url-tab') {
                const url = formData.get('media_url');
                if (!url || !url.trim()) {
                    alert('Por favor, informe a URL do vídeo ou áudio.');
                    return;
                }
                formData.delete('file');
            } else {
                const file = fileInput.files[0];
                if (!file) {
                    alert('Por favor, selecione um arquivo de vídeo ou áudio.');
                    return;
                }
                formData.delete('media_url');
            }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span>⏳ Enviando e iniciando processamento...</span>';

            try {
                const resp = await fetch('/api/jobs', {
                    method: 'POST',
                    body: formData
                });

                if (!resp.ok) {
                    const errData = await resp.json().catch(() => ({}));
                    throw new Error(errData.detail || 'Falha ao criar tarefa.');
                }

                const data = await resp.json();
                window.location.href = data.redirect_url;
            } catch (err) {
                alert('Erro: ' + err.message);
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>🚀 Gerar Legendas</span>';
            }
        });
    }
});
