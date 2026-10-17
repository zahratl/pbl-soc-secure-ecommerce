
/*
 * KURO WEAR - LIVE PRODUCT SEARCH
 * Produk langsung dari database Flask.
 */

document.addEventListener("DOMContentLoaded", function () {

    const form = document.getElementById("kuroSearchForm");
    const input = document.getElementById("kuroSearchInput");
    const suggestions = document.getElementById(
        "kuroSearchSuggestions"
    );

    if (!form || !input || !suggestions) {
        return;
    }

    let searchTimer = null;
    let controller = null;

    const endpoint = "/search/suggest";

    function closeSuggestions() {
        suggestions.hidden = true;
        suggestions.replaceChildren();
    }

    function showMessage(message) {
        suggestions.replaceChildren();

        const empty = document.createElement("div");
        empty.className = "kuro-search-empty";
        empty.textContent = message;

        suggestions.appendChild(empty);
        suggestions.hidden = false;
    }

    function createSuggestion(product) {

        const link = document.createElement("a");

        link.href = product.url;
        link.className = "kuro-search-item";
        link.setAttribute("role", "option");

        if (product.image) {
            const image = document.createElement("img");

            image.src = product.image;
            image.alt = product.name;
            image.className = "kuro-search-thumb";

            link.appendChild(image);
        }

        const details = document.createElement("div");
        details.className = "kuro-search-details";

        const name = document.createElement("div");
        name.className = "kuro-search-name";
        name.textContent = product.name;

        const price = document.createElement("div");
        price.className = "kuro-search-price";
        price.textContent = product.price;

        details.appendChild(name);
        details.appendChild(price);

        link.appendChild(details);

        return link;
    }

    async function findSuggestions(keyword) {

        if (controller) {
            controller.abort();
        }

        controller = new AbortController();

        try {
            const response = await fetch(
                endpoint + "?q=" + encodeURIComponent(keyword),
                {
                    signal: controller.signal,
                    credentials: "same-origin"
                }
            );

            if (!response.ok) {
                throw new Error("Search request failed");
            }

            const result = await response.json();

            // Abaikan hasil lama jika input sudah berubah
            if (input.value.trim() !== keyword) {
                return;
            }

            suggestions.replaceChildren();

            if (!result.items || result.items.length === 0) {
                showMessage("Produk tidak ditemukan.");
                return;
            }

            const heading = document.createElement("div");
            heading.className = "kuro-search-heading";
            heading.textContent = "PRODUK YANG COCOK";

            suggestions.appendChild(heading);

            result.items.forEach(product => {
                suggestions.appendChild(
                    createSuggestion(product)
                );
            });

            suggestions.hidden = false;

        } catch (error) {
            if (error.name !== "AbortError") {
                closeSuggestions();
            }
        }
    }

    input.addEventListener("input", function () {

        clearTimeout(searchTimer);

        const keyword = input.value.trim();

        if (controller) {
            controller.abort();
        }

        if (keyword.length < 2) {
            closeSuggestions();
            return;
        }

        searchTimer = setTimeout(() => {
            findSuggestions(keyword);
        }, 250);
    });

    // Klik di luar search bar menutup dropdown
    document.addEventListener("click", function (event) {
        if (!form.contains(event.target)) {
            closeSuggestions();
        }
    });

    // Escape untuk menutup dropdown
    input.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeSuggestions();
        }

        if (event.key === "ArrowDown" && !suggestions.hidden) {
            const first = suggestions.querySelector("a");

            if (first) {
                event.preventDefault();
                first.focus();
            }
        }
    });

    suggestions.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeSuggestions();
            input.focus();
        }
    });

    // Submit normal menuju halaman hasil pencarian
    form.addEventListener("submit", function (event) {
        if (!input.value.trim()) {
            event.preventDefault();
            input.focus();
        }
    });

});