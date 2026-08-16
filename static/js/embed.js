(function() {
    if (window.KairosWidget) return;
    
    const KairosWidget = {
        init: function(options) {
            this.options = options || {};
            this.mode = this.options.mode || 'inline';
            this.url = this.options.url;
            this.parentElement = this.options.parentElement || document.body;
            
            let finalUrl = this.url;
            if (this.options.metadata) {
                finalUrl += (finalUrl.includes('?') ? '&' : '?') + 'metadata=' + encodeURIComponent(JSON.stringify(this.options.metadata));
            }
            
            const urlParams = new URLSearchParams(window.location.search);
            ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'].forEach(param => {
                if (urlParams.has(param)) {
                    finalUrl += (finalUrl.includes('?') ? '&' : '?') + param + '=' + encodeURIComponent(urlParams.get(param));
                }
            });
            
            if (this.mode === 'inline') {
                this.renderInline(finalUrl);
            } else if (this.mode === 'popup') {
                this.renderPopup(finalUrl);
            } else if (this.mode === 'floating') {
                this.renderFloating(finalUrl);
            }
            
            window.addEventListener('message', this.handleMessage.bind(this));
        },
        
        renderInline: function(url) {
            const container = document.createElement('div');
            container.style.width = '100%';
            container.style.height = '600px';
            
            const iframe = document.createElement('iframe');
            iframe.src = url;
            iframe.style.width = '100%';
            iframe.style.height = '100%';
            iframe.style.border = 'none';
            iframe.id = 'kairos-iframe-inline';
            
            container.appendChild(iframe);
            this.parentElement.appendChild(container);
        },
        
        renderPopup: function(url) {
            const overlay = document.createElement('div');
            overlay.style.display = 'none';
            overlay.style.position = 'fixed';
            overlay.style.top = '0';
            overlay.style.left = '0';
            overlay.style.width = '100%';
            overlay.style.height = '100%';
            overlay.style.backgroundColor = 'rgba(0,0,0,0.5)';
            overlay.style.zIndex = '999999';
            overlay.style.justifyContent = 'center';
            overlay.style.alignItems = 'center';
            
            const content = document.createElement('div');
            content.style.width = '80%';
            content.style.maxWidth = '1000px';
            content.style.height = '80%';
            content.style.backgroundColor = '#fff';
            content.style.borderRadius = '8px';
            content.style.overflow = 'hidden';
            content.style.position = 'relative';
            
            const closeBtn = document.createElement('button');
            closeBtn.innerHTML = '&times;';
            closeBtn.style.position = 'absolute';
            closeBtn.style.top = '10px';
            closeBtn.style.right = '15px';
            closeBtn.style.border = 'none';
            closeBtn.style.background = 'none';
            closeBtn.style.fontSize = '24px';
            closeBtn.style.cursor = 'pointer';
            closeBtn.onclick = () => { overlay.style.display = 'none'; };
            
            const iframe = document.createElement('iframe');
            iframe.src = url;
            iframe.style.width = '100%';
            iframe.style.height = '100%';
            iframe.style.border = 'none';
            
            content.appendChild(closeBtn);
            content.appendChild(iframe);
            overlay.appendChild(content);
            document.body.appendChild(overlay);
            
            this.openPopup = () => { overlay.style.display = 'flex'; };
        },
        
        renderFloating: function(url) {
            this.renderPopup(url);
            
            const button = document.createElement('button');
            button.innerHTML = 'Book Time';
            button.style.position = 'fixed';
            button.style.bottom = '20px';
            button.style.right = '20px';
            button.style.padding = '12px 24px';
            button.style.backgroundColor = '#000';
            button.style.color = '#fff';
            button.style.border = 'none';
            button.style.borderRadius = '24px';
            button.style.cursor = 'pointer';
            button.style.zIndex = '999998';
            button.style.boxShadow = '0 4px 6px rgba(0,0,0,0.1)';
            
            button.onclick = () => { this.openPopup(); };
            document.body.appendChild(button);
        },
        
        handleMessage: function(event) {
            if (event.data && event.data.type === 'kairos:resize' && this.mode === 'inline') {
                const iframe = document.getElementById('kairos-iframe-inline');
                if (iframe) {
                    iframe.style.height = event.data.height + 'px';
                }
            }
        }
    };
    
    window.KairosWidget = KairosWidget;
})();
