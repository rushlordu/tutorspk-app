// Shared frontend helpers for TutorsOnline.pk.
// Kept intentionally small and dependency-free.

(function () {
  const fallbackAvatar = window.TUTORPK_DEFAULT_AVATAR || "/static/images/default-avatar.svg";

  function useFallbackImage(img) {
    if (!img || img.dataset.fallbackApplied === "1") return;
    img.dataset.fallbackApplied = "1";
    img.src = img.dataset.fallback || fallbackAvatar;
  }

  document.addEventListener("error", function (event) {
    const target = event.target;
    if (target && target.tagName === "IMG") {
      useFallbackImage(target);
    }
  }, true);


  function bindImagePreviews() {
    document.querySelectorAll("[data-image-preview-input]").forEach(function (input) {
      input.addEventListener("change", function () {
        const key = input.getAttribute("data-image-preview-input");
        const file = input.files && input.files[0];
        if (!file || !file.type || file.type.indexOf("image/") !== 0) return;

        const previewUrl = URL.createObjectURL(file);
        document.querySelectorAll('[data-image-preview-target="' + key + '"]').forEach(function (img) {
          img.src = previewUrl;
          img.dataset.fallbackApplied = "0";
        });
      });
    });
  }

  function bindSafeExternalLinks() {
    document.querySelectorAll('a[target="_blank"]').forEach(function (link) {
      const rel = (link.getAttribute("rel") || "").split(/\s+/);
      if (rel.indexOf("noopener") === -1) rel.push("noopener");
      if (rel.indexOf("noreferrer") === -1) rel.push("noreferrer");
      link.setAttribute("rel", rel.join(" ").trim());
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("img").forEach(function (img) {
      img.loading = img.loading || "lazy";
      img.decoding = img.decoding || "async";
      if (!img.getAttribute("src")) {
        useFallbackImage(img);
      }
    });
    bindImagePreviews();
    bindSafeExternalLinks();
  });
}());
