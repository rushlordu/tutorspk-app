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

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("img").forEach(function (img) {
      img.loading = img.loading || "lazy";
      img.decoding = img.decoding || "async";
      if (!img.getAttribute("src")) {
        useFallbackImage(img);
      }
    });
  });
}());
