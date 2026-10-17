
/* KURO WEAR - FILTER ALL, WOMEN, MAN */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const root = document.querySelector(".kw-collection-page");
        if (!root) return;

        const grid = root.querySelector("[data-kw-grid]");
        const empty = root.querySelector("[data-kw-empty]");
        const visibleCounter = root.querySelector("[data-kw-visible]");
        const categoryInputs = Array.from(
            root.querySelectorAll('[data-kw-category-checkbox]')
        );
        const genderInputs = Array.from(
            root.querySelectorAll('[data-kw-gender]')
        );
        const sizeButtons = Array.from(
            root.querySelectorAll("[data-kw-size]")
        );
        const quickButtons = Array.from(
            root.querySelectorAll("[data-kw-quick]")
        );
        const categoryCards = Array.from(
            root.querySelectorAll("[data-kw-category-card]")
        );
        const sortSelect = root.querySelector("[data-kw-sort]");

        let quickFilter = "all";

        function cards() {
            return Array.from(
                grid.querySelectorAll("[data-kw-product]")
            );
        }

        function selectedGenders() {
            const chosen = genderInputs
                .filter(input => input.checked)
                .map(input => input.value);

            return chosen.includes("all") ? [] : chosen;
        }

        function matchesQuick(category, gender) {
            if (quickFilter === "all") return true;
            if (quickFilter === "unisex") {
                return gender === "unisex";
            }
            if (quickFilter === "skirts_pants") {
                return ["Skirts", "Pants"].includes(category);
            }
            if (quickFilter === "Outer_Knit") {
                return ["Outer", "Knit"].includes(category);
            }
            if (quickFilter === "Shirts_Tee") {
                return ["Shirts", "T-Shirts"].includes(category);
            }
            return category === quickFilter;
        }

        function applyFilters() {
            const categories = categoryInputs
                .filter(input => input.checked)
                .map(input => input.value);

            const genders = selectedGenders();

            const sizes = sizeButtons
                .filter(btn => btn.classList.contains("kw-active"))
                .map(btn => btn.dataset.kwSize);

            let count = 0;

            cards().forEach(card => {
                const category = card.dataset.kwCategory;
                const gender = card.dataset.kwGender;
                const productSizes = (
                    card.dataset.kwSizes || ""
                ).split(",").filter(Boolean);

                const categoryOk = categories.includes(category);

                const genderOk = (
                    genders.length === 0 ||
                    genders.includes(gender)
                );

                const sizeOk = (
                    sizes.length === 0 ||
                    sizes.some(size => productSizes.includes(size))
                );

                const visible = (
                    categoryOk &&
                    genderOk &&
                    sizeOk &&
                    matchesQuick(category, gender)
                );

                card.hidden = !visible;

                if (visible) count++;
            });

            if (visibleCounter) {
                visibleCounter.textContent = String(count);
            }

            if (empty) {
                empty.hidden = count !== 0;
            }
        }

        function resetQuickButtons() {
            quickFilter = "all";
            quickButtons.forEach(button => {
                button.classList.toggle(
                    "kw-active",
                    button.dataset.kwQuick === "all"
                );
            });
        }

        function resetAll() {
            categoryInputs.forEach(input => {
                input.checked = true;
            });

            genderInputs.forEach(input => {
                input.checked = input.value === "all";
            });

            sizeButtons.forEach(button => {
                button.classList.remove("kw-active");
                button.setAttribute("aria-pressed", "false");
            });

            categoryCards.forEach(card => {
                card.classList.remove("kw-selected");
            });

            resetQuickButtons();

            if (sortSelect) {
                sortSelect.value = "newest";
                sortCards("newest");
            }

            applyFilters();
        }

        function sortCards(sortValue) {
            const items = cards();

            items.sort((a, b) => {
                if (sortValue === "price_asc") {
                    return Number(a.dataset.kwPrice) -
                           Number(b.dataset.kwPrice);
                }

                if (sortValue === "price_desc") {
                    return Number(b.dataset.kwPrice) -
                           Number(a.dataset.kwPrice);
                }

                return Number(b.dataset.kwId) -
                       Number(a.dataset.kwId);
            });

            items.forEach(item => grid.appendChild(item));
        }

        genderInputs.forEach(input => {
            input.addEventListener("change", function () {
                if (this.value === "all" && this.checked) {
                    genderInputs.forEach(other => {
                        other.checked = other === this;
                    });
                } else {
                    const allInput = genderInputs.find(
                        item => item.value === "all"
                    );

                    if (allInput) allInput.checked = false;

                    if (!genderInputs.some(item => item.checked)) {
                        if (allInput) allInput.checked = true;
                    }
                }

                applyFilters();
            });
        });

        categoryInputs.forEach(input => {
            input.addEventListener("change", function () {
                categoryCards.forEach(card => {
                    card.classList.remove("kw-selected");
                });
                applyFilters();
            });
        });

        sizeButtons.forEach(button => {
            button.addEventListener("click", function () {
                const active = this.classList.toggle("kw-active");
                this.setAttribute("aria-pressed", String(active));
                applyFilters();
            });
        });

        quickButtons.forEach(button => {
            button.addEventListener("click", function () {
                quickFilter = this.dataset.kwQuick;

                quickButtons.forEach(other => {
                    other.classList.toggle(
                        "kw-active",
                        other === this
                    );
                });

                categoryInputs.forEach(input => {
                    input.checked = true;
                });

                categoryCards.forEach(card => {
                    card.classList.remove("kw-selected");
                });

                applyFilters();
            });
        });

        categoryCards.forEach(button => {
            button.addEventListener("click", function () {
                const category = this.dataset.kwCategoryCard;

                categoryInputs.forEach(input => {
                    input.checked = input.value === category;
                });

                resetQuickButtons();

                categoryCards.forEach(card => {
                    card.classList.toggle(
                        "kw-selected",
                        card === this
                    );
                });

                applyFilters();

                const content = root.querySelector(".kw-content");
                if (content) {
                    content.scrollIntoView({
                        behavior: "smooth",
                        block: "start"
                    });
                }
            });
        });

        const resetButton = root.querySelector("[data-kw-reset]");
        if (resetButton) {
            resetButton.addEventListener("click", resetAll);
        }

        if (sortSelect) {
            sortSelect.addEventListener("change", function () {
                sortCards(this.value);
                applyFilters();
            });
        }

        // Parameter URL dari link kategori
        const params = new URLSearchParams(location.search);
        const categoryParam = params.get("category");
        const genderParam = params.get("gender");

        const aliases = {
            outer: "Outer",
            skirts: "Skirts",
            pants: "Pants",
            shirts: "Shirts",
            tshirts: "T-Shirts",
            "t-shirts": "T-Shirts",
            knit: "Knit"
        };

        if (categoryParam) {
            const category = aliases[
                categoryParam.trim().toLowerCase()
            ];

            if (category) {
                categoryInputs.forEach(input => {
                    input.checked = input.value === category;
                });
            }
        }

        if (genderParam) {
            const gender = genderParam.trim().toLowerCase();

            if (["men", "women", "unisex"].includes(gender)) {
                genderInputs.forEach(input => {
                    input.checked = input.value === gender;
                });
            }
        }

        if (sortSelect) {
            sortCards(sortSelect.value);
        }

        applyFilters();
    });
})();