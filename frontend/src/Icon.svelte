<script>
  /** Line-style icons — stroke SVG only, no emoji. */
  let { name, size = 14, class: className = '' } = $props();
</script>

<svg
  class={`ui-icon ${className}`}
  width={size}
  height={size}
  viewBox="0 0 16 16"
  fill="none"
  stroke="currentColor"
  stroke-width="1.25"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === 'folder'}
    <path d="M2.5 4.5h4L7.5 6H13.5v6.5H2.5V4.5z" />
  {:else if name === 'chevron-right'}
    <path d="M6 4.5 10 8l-4 3.5" />
  {:else if name === 'chevron-down'}
    <path d="M4.5 6 8 9.5 11.5 6" />
  {:else if name === 'chevron-left'}
    <path d="M10 4.5 6 8l4 3.5" />
  {:else if name === 'plus'}
    <path d="M8 3.5v9M3.5 8h9" />
  {:else if name === 'corner-up-left'}
    <path d="M6 3.5H3.5V6M3.5 3.5 9 9" />
  {:else if name === 'refresh'}
    <path d="M12.5 4.5A5.5 5.5 0 1 0 7 13" />
    <path d="M12.5 2.5v2h-2" />
  {:else if name === 'x'}
    <path d="M4.5 4.5 11.5 11.5M11.5 4.5 4.5 11.5" />
  {:else if name === 'check'}
    <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
  {:else if name === 'loom'}
    <path d="M2.5 5h11M2.5 8h9M2.5 11h11" />
  {:else if name === 'inbox'}
    <path d="M2.5 4 4 2.5h8L13.5 4v8.5L12 14H4l-1.5-1.5V4z" />
    <path d="M2.5 9.5h3l1 1.5h3l1-1.5h3" />
  {:else if name === 'webhook'}
    <circle cx="5" cy="11" r="1.5" />
    <circle cx="11" cy="11" r="1.5" />
    <circle cx="8" cy="4" r="1.5" />
    <path d="M6.5 10 7.5 5.5M9.5 5.5 10.5 10M6.5 11h3" />
  {:else if name === 'file-text'}
    <path d="M4 2.5h5l3 3V13.5L11.5 14h-7.5L4 13.5z" />
    <path d="M9 2.5V6h3.5" />
    <path d="M6 8.5h4M6 11h4" />
  {/if}
</svg>
