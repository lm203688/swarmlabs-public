/**
 * i18n.js — SwarmLabs 国际化模块
 *
 * 用法：
 *   1. HTML 元素加 data-i18n="key" 属性
 *   2. 页面底部引入 <script src="/static/i18n.js"></script>
 *   3. 语言切换按钮: <button onclick="setLang('en')">EN</button>
 *
 * 支持语言: zh-CN, en
 * 翻译数据: data/i18n.json
 */
(function () {
    "use strict";

    const I18N_URL = "/data/i18n.json";
    const STORAGE_KEY = "swarmlabs_lang";

    let translations = null;
    let currentLang = localStorage.getItem(STORAGE_KEY) || "zh-CN";

    // 尝试从 JSON 加载
    async function loadTranslations() {
        try {
            const resp = await fetch(I18N_URL);
            if (resp.ok) {
                translations = await resp.json();
            }
        } catch (e) {
            // 静默降级
        }
    }

    function t(key, lang) {
        lang = lang || currentLang;
        if (!translations || !translations[lang]) return key;
        return translations[lang][key] || translations["zh-CN"][key] || key;
    }

    function applyTranslations(lang) {
        lang = lang || currentLang;
        currentLang = lang;
        localStorage.setItem(STORAGE_KEY, lang);
        document.documentElement.lang = lang;

        if (!translations) return;

        // 更新所有 data-i18n 元素
        document.querySelectorAll("[data-i18n]").forEach(el => {
            const key = el.getAttribute("data-i18n");
            const val = t(key, lang);
            if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
                el.placeholder = val;
            } else if (el.hasAttribute("data-i18n-html")) {
                el.innerHTML = val;
            } else {
                el.textContent = val;
            }
        });

        // 更新 data-i18n-attr 属性
        document.querySelectorAll("[data-i18n-attr]").forEach(el => {
            const parts = el.getAttribute("data-i18n-attr").split(":");
            const key = parts[1] || parts[0];
            const attr = parts[0];
            el.setAttribute(attr, t(key, lang));
        });

        // 触发事件
        window.dispatchEvent(new CustomEvent("langChanged", { detail: { lang } }));
    }

    function setLang(lang) {
        if (translations && (lang in translations)) {
            applyTranslations(lang);
        }
    }

    function getLang() {
        return currentLang;
    }

    // 创建语言切换按钮
    function createLangToggle() {
        if (document.querySelector("#lang-toggle")) return; // 已存在

        const btn = document.createElement("button");
        btn.id = "lang-toggle";
        btn.className = "i18n-toggle";
        btn.style.cssText = "position:fixed;top:12px;right:12px;z-index:10000;background:rgba(30,41,59,0.85);color:#e2e8f0;border:1px solid rgba(148,163,184,0.3);border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer;backdrop-filter:blur(4px);";
        btn.textContent = currentLang === "zh-CN" ? "EN" : "中文";
        btn.title = "切换语言 / Switch Language";
        btn.addEventListener("click", function () {
            const next = currentLang === "zh-CN" ? "en" : "zh-CN";
            setLang(next);
            btn.textContent = next === "zh-CN" ? "EN" : "中文";
        });
        document.body.appendChild(btn);
    }

    // 启动
    async function init() {
        await loadTranslations();
        applyTranslations(currentLang);
        createLangToggle();
    }

    // 暴露全局 API
    window.SwarmLabsI18n = { t, setLang, getLang, applyTranslations, load: loadTranslations };

    // DOM 就绪后初始化
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();