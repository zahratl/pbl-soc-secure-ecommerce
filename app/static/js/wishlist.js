/*
 * KURO WEAR - Wishlist
 * Tersimpan per akun customer melalui Flask.
 */

function showWishlistMessage(message) {
    let toast = document.getElementById("kuro-wishlist-toast");

    if (!toast) {
        toast = document.createElement("div");
        toast.id = "kuro-wishlist-toast";
        toast.setAttribute("role", "status");

        Object.assign(toast.style, {
            position: "fixed",
            right: "20px",
            bottom: "25px",
            zIndex: "99999",
            background: "#1A1B22",
            color: "#FFFFFF",
            padding: "14px 20px",
            fontSize: "13px",
            boxShadow: "0 4px 15px rgba(0,0,0,.2)",
            borderLeft: "4px solid #B7000C",
            maxWidth: "330px"
        });

        document.body.appendChild(toast);
    }

    toast.textContent = message;
    toast.hidden = false;

    clearTimeout(showWishlistMessage.timeoutId);

    showWishlistMessage.timeoutId = setTimeout(() => {
        toast.hidden = true;
    }, 2500);
}


function updateWishlistButtons(productId, isSaved) {
    document.querySelectorAll(
        "[data-wishlist-product]"
    ).forEach(button => {

        if (Number(button.dataset.wishlistProduct) !== productId) {
            return;
        }

        const icon = button.querySelector("i");

        if (!icon) {
            return;
        }

        icon.classList.remove(
            "bi-heart",
            "bi-heart-fill",
            "text-danger",
            "text-dark"
        );

        icon.classList.add(
            isSaved ? "bi-heart-fill" : "bi-heart"
        );

        icon.style.color = isSaved ? "#B7000C" : "#1A1B22";

        button.setAttribute("aria-pressed", String(isSaved));

        button.title = isSaved
            ? "Hapus dari Wishlist"
            : "Tambah ke Wishlist";
    });
}


document.addEventListener("click", async function (event) {

    const button = event.target.closest(
        "[data-wishlist-product]"
    );

    if (!button) {
        return;
    }

    event.preventDefault();
    event.stopPropagation();

    if (button.disabled) {
        return;
    }

    const productId = Number(button.dataset.wishlistProduct);
    const endpoint = button.dataset.wishlistUrl;
    const csrfToken = button.dataset.csrf;

    if (!productId || !endpoint || !csrfToken) {
        showWishlistMessage("Wishlist belum siap. Refresh halaman.");
        return;
    }

    button.disabled = true;

    try {
        const response = await fetch(endpoint, {
            method: "POST",

            credentials: "same-origin",

            headers: {
                "X-CSRFToken": csrfToken,
                "Accept": "application/json"
            }
        });

        if (response.status === 401) {
            window.location.href = (
                button.dataset.loginUrl || "/auth/login"
            );
            return;
        }

        if (!response.ok) {
            showWishlistMessage(
                response.status === 400
                    ? "Sesi form berakhir. Refresh halaman."
                    : "Gagal memperbarui wishlist."
            );
            return;
        }

        const result = await response.json();

        if (!result.success) {
            showWishlistMessage(
                result.message || "Wishlist gagal diperbarui."
            );
            return;
        }

        updateWishlistButtons(
            productId,
            result.wishlisted
        );

        document.querySelectorAll(
            "[data-wishlist-count]"
        ).forEach(element => {
            element.textContent = result.count;
        });

        showWishlistMessage(result.message);

        // Di halaman Wishlist, setelah produk dihapus,
        // refresh agar kartu hilang dari daftar.
        if (
            document.getElementById("wishlist-page")
            && !result.wishlisted
        ) {
            window.location.reload();
        }

    } catch (error) {
        showWishlistMessage(
            "Koneksi bermasalah. Silakan coba lagi."
        );

    } finally {
        button.disabled = false;
    }
});