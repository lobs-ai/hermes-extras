// Services page for the Hermes desktop app. Lists the web services the agent has
// registered on the backend machine (`hermes tailnet-services add`) with a live
// health dot, the tailnet URL, a description, the source repo and an Open button.
// Data comes from this package's dashboard/plugin_api.py via ctx.rest.

import {
  Button,
  CopyButton,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  StatusDot,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'tailnet-services'
const PATH = '/services'

// Disk plugins aren't scanned by the app's Tailwind build, so any utility class
// core doesn't already use may not exist. Layout lives here, colours come only
// from theme variables so every skin repaints it.
const CSS = `
.tsvc-page{display:flex;flex-direction:column;gap:12px;height:100%;overflow:auto;padding:20px 24px;color:var(--ui-text-primary)}
.tsvc-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px}
.tsvc-title{font-size:15px;font-weight:600;line-height:20px}
.tsvc-sub{font-size:12px;color:var(--ui-text-tertiary);margin-top:2px}
.tsvc-list{display:flex;flex-direction:column;border-top:1px solid var(--ui-stroke-secondary)}
.tsvc-row{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:10px;align-items:start;padding:12px 2px;border-bottom:1px solid var(--ui-stroke-secondary)}
.tsvc-dot{display:flex;align-items:center;justify-content:center;height:20px}
.tsvc-dot>span{width:8px;height:8px}
.tsvc-main{min-width:0;display:flex;flex-direction:column;gap:3px}
.tsvc-name{font-size:13px;font-weight:600;line-height:20px}
.tsvc-desc{font-size:12px;color:var(--ui-text-secondary);line-height:17px}
.tsvc-meta{display:flex;align-items:center;gap:6px;min-width:0;font-size:11.5px;color:var(--ui-text-tertiary)}
.tsvc-url{font-family:var(--font-mono,monospace);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.tsvc-label{color:var(--ui-text-quaternary);flex-shrink:0}
.tsvc-actions{display:flex;align-items:center;gap:6px;height:20px}
.tsvc-note{font-size:12px;color:var(--ui-text-tertiary)}
`

function openUrl(ctx, url, name) {
  // host.openPreview isn't in the SDK today; if it lands, services open in the
  // app's own preview instead of the system browser.
  return typeof host.openPreview === 'function' ? host.openPreview(url, name) : ctx.os.openExternal(url)
}

function healthTip(health) {
  if (!health) return 'Not checked yet'
  if (health.up) return `Up: HTTP ${health.status} in ${health.latency_ms} ms`
  return `Down: nothing answered on 127.0.0.1 (${health.error || 'no response'})`
}

function ServiceRow({ ctx, service }) {
  const up = Boolean(service.health && service.health.up)
  const reach = service.mode === 'serve' ? 'tailscale serve' : 'direct'

  return jsxs('div', {
    className: 'tsvc-row',
    'data-service': service.name,
    children: [
      jsx(Tip, {
        label: healthTip(service.health),
        children: jsx('span', { className: 'tsvc-dot', children: jsx(StatusDot, { tone: up ? 'good' : 'bad' }) })
      }),
      jsxs('div', {
        className: 'tsvc-main',
        children: [
          jsx('div', { className: 'tsvc-name', children: service.name }),
          service.description && jsx('div', { className: 'tsvc-desc', children: service.description }),
          service.url &&
            jsxs('div', {
              className: 'tsvc-meta',
              children: [
                jsx('span', { className: 'tsvc-url', title: `${service.url} (${reach}, port ${service.port})`, children: service.url }),
                jsx(CopyButton, { appearance: 'icon', text: service.url, label: 'Copy URL' })
              ]
            }),
          service.repo &&
            jsxs('div', {
              className: 'tsvc-meta',
              children: [
                jsx('span', { className: 'tsvc-label', children: 'repo' }),
                service.repo_url
                  ? jsx(Button, {
                      variant: 'link',
                      size: 'inline',
                      onClick: () => void ctx.os.openExternal(service.repo_url),
                      children: service.repo
                    })
                  : jsx('span', { className: 'tsvc-url', children: service.repo })
              ]
            })
        ]
      }),
      jsx('div', {
        className: 'tsvc-actions',
        children: jsx(Button, {
          variant: 'outline',
          size: 'xs',
          disabled: !service.url,
          onClick: () => void openUrl(ctx, service.url, service.name),
          children: 'Open'
        })
      })
    ]
  })
}

function ServicesPage({ ctx }) {
  const query = useQuery({
    queryKey: [ID, 'services'],
    queryFn: () => ctx.rest('/services', { timeoutMs: 15000 }),
    refetchInterval: 15000,
    refetchOnWindowFocus: true
  })

  const data = query.data
  const services = (data && data.services) || []
  const host_ = data && data.host

  let body
  if (query.isLoading) {
    body = jsx('div', { className: 'tsvc-note', children: jsx(GlyphSpinner, { ariaLabel: 'Loading services' }) })
  } else if (query.isError) {
    body = jsx(ErrorState, {
      title: 'Services unavailable',
      description:
        'The backend did not answer. On the machine running Hermes: hermes plugins enable tailnet-services, then restart the dashboard backend. ' +
        String((query.error && query.error.message) || '')
    })
  } else if (services.length === 0) {
    body = jsx(EmptyState, {
      title: 'No services registered',
      description: 'Ask the agent to register one: hermes tailnet-services add NAME --port N'
    })
  } else {
    body = jsx('div', {
      className: 'tsvc-list',
      children: services.map(service => jsx(ServiceRow, { ctx, service }, service.name))
    })
  }

  const upCount = services.filter(s => s.health && s.health.up).length

  return jsxs('div', {
    className: 'tsvc-page',
    children: [
      jsxs('div', {
        className: 'tsvc-head',
        children: [
          jsxs('div', {
            children: [
              jsx('div', { className: 'tsvc-title', children: 'Services' }),
              jsx('div', {
                className: 'tsvc-sub',
                children: services.length
                  ? `${upCount} of ${services.length} up${host_ ? ` on ${host_}` : ''}`
                  : host_ || 'Web services on the Hermes machine'
              })
            ]
          }),
          jsx(Button, {
            variant: 'ghost',
            size: 'xs',
            disabled: query.isFetching,
            onClick: () => void query.refetch(),
            children: query.isFetching ? 'Checking…' : 'Refresh'
          })
        ]
      }),
      data && data.error && jsx('div', { className: 'tsvc-note', children: data.error }),
      body
    ]
  })
}

export default {
  id: ID,
  name: 'Services',
  description: 'Web services on the Hermes machine, reachable over the tailnet.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())

    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(ServicesPage, { ctx }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 60, data: { path: PATH, label: 'Services', codicon: 'server' } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'tailnet-services.open',
          label: 'Services: Open',
          keywords: ['services', 'tailnet', 'ports', 'tailscale'],
          run: () => host.navigate(PATH)
        }
      }
    ])
  }
}
