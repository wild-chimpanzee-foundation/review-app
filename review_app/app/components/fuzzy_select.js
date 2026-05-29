export default {
  props: ["options"],
  template: `
    <q-select
      ref="qRef"
      :options="filteredOptions"
      @filter="filterFn"
      @popup-show="addClass"
      @popup-hide="removeClass"
    >
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-select>
  `,
  data() {
    return {
      initialOptions: this.options,
      filteredOptions: this.options,
    };
  },
  methods: {
    filterFn(val, update, abort) {
      update(
        () => (this.filteredOptions = val ? this.findFilteredOptions() : this.initialOptions),
        (ref) => {
          // When the filter narrows to exactly one option, auto-select it.
          // Single-select only: auto-adding in multiple mode would be surprising.
          if (this.$attrs.multiple || !val || this.filteredOptions.length !== 1) return;
          const opt = this.filteredOptions[0];
          if (ref.modelValue && ref.modelValue.value === opt.value) return;
          ref.toggleOption(opt);
          // After committing the single match, drop focus so trailing keystrokes
          // don't re-open the filter, and briefly suppress global shortcuts so
          // those same keystrokes aren't interpreted as actions (A/B/C/M/Enter).
          window.__suppressShortcutsUntil = Date.now() + 400;
          this.$nextTick(() => {
            if (ref.blur) ref.blur();
            document.activeElement?.blur();
          });
        }
      );
    },
    subseqScore(needle, haystack) {
      // Anchored subsequence: the first needle char must begin a word in the
      // haystack (index 0 or preceded by a non-letter/digit), then the rest must
      // appear in order. Returns null if no anchor matches, else a score where
      // lower is a tighter/earlier match. Word boundary is Unicode-aware so
      // accented and hyphenated labels behave correctly.
      const first = needle[0];
      let best = null;
      for (let a = 0; a < haystack.length; a++) {
        if (haystack[a] !== first) continue;
        const isWordStart = a === 0 || !/[\p{L}\p{N}]/u.test(haystack[a - 1]);
        if (!isWordStart) continue;
        let i = 1;
        let lastIdx = a;
        for (let j = a + 1; j < haystack.length && i < needle.length; j++) {
          if (haystack[j] === needle[i]) {
            lastIdx = j;
            i++;
          }
        }
        if (i < needle.length) continue; // rest of needle not found from this anchor
        const score = (lastIdx - a) * 100 + a; // span first, then earliness
        if (best === null || score < best) best = score;
      }
      return best;
    },
    findFilteredOptions() {
      const needle = this.$el.querySelector("input[type=search]")?.value.toLocaleLowerCase();
      if (!needle) return this.initialOptions;
      return this.initialOptions
        .map((v) => ({ v, score: this.subseqScore(needle, String(v.label).toLocaleLowerCase()) }))
        .filter((x) => x.score !== null)
        .sort((a, b) => a.score - b.score)
        .map((x) => x.v);
    },
    addClass() {
      // NOTE: prevent the page from scrolling when the select popup is closed (#5031)
      document.documentElement.classList.add("nicegui-select-popup-open");
    },
    async removeClass() {
      await this.$nextTick();
      document.documentElement.classList.remove("nicegui-select-popup-open");
    },
  },
  updated() {
    if (!this.$attrs.multiple) return;
    const newFilteredOptions = this.findFilteredOptions();
    if (newFilteredOptions.length !== this.filteredOptions.length) {
      this.filteredOptions = newFilteredOptions;
    }
  },
  unmounted() {
    this.removeClass();
  },
  watch: {
    options: {
      handler(newOptions) {
        this.initialOptions = newOptions;
        this.filteredOptions = newOptions;
      },
      immediate: true,
    },
  },
};
