// Standalone React Component


interface Metrics {
  tokens_total: number;
  model: string;
  vram_usage: string;
}

interface TokenSidebarProps {
  metrics: Metrics | null;
}

const TokenSidebar = ({ metrics }) => {
  if (!metrics) {
    return (
      <div className="token-sidebar" style={{ backgroundColor: 'var(--c-card)', color: 'var(--c-text-primary)', border: '1px solid var(--c-card-border)', padding: '16px', borderRadius: '8px', width: '256px', boxShadow: 'var(--shadow-card)' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 'bold', textTransform: 'uppercase', marginBottom: '8px', color: 'var(--c-text-muted)' }}>Telemetry</h3>
        <div style={{ fontStyle: 'italic', fontSize: '14px', color: 'var(--c-text-muted)' }}>Awaiting task completion...</div>
      </div>
    );
  }

  // Estimated Cost (assuming hypothetical $0.0001 per local compute cycle token for UI demo)
  const estCost = ((metrics.tokens_total || 0) * 0.0001).toFixed(4);

  return (
    <div className="token-sidebar" style={{ backgroundColor: 'var(--c-card)', border: '1px solid var(--c-card-border)', color: 'var(--c-text-primary)', padding: '16px', borderRadius: '8px', width: '256px', boxShadow: 'var(--shadow-card)' }}>
      <h3 style={{ fontSize: '12px', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--c-text-muted)' }}>
        <span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--c-success)', animation: 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite' }}></span>
        Telemetry Active
      </h3>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {/* Token Meter */}
        <div className="metric-group">
          <div style={{ fontSize: '12px', marginBottom: '4px', color: 'var(--c-text-muted)' }}>Total Tokens</div>
          <div className="mono" style={{ fontSize: '24px', color: 'var(--c-success)' }}>
            {metrics.tokens_total.toLocaleString()}
          </div>
        </div>

        {/* Model Info */}
        <div className="metric-group" style={{ borderTop: '1px solid var(--c-card-border)', paddingTop: '12px' }}>
          <div style={{ fontSize: '12px', marginBottom: '4px', color: 'var(--c-text-muted)' }}>Compute Core</div>
          <div className="mono" style={{ fontSize: '14px', color: 'var(--c-info)' }}>
            {metrics.model}
          </div>
        </div>

        {/* VRAM Gauge */}
        <div className="metric-group" style={{ borderTop: '1px solid var(--c-card-border)', paddingTop: '12px' }}>
          <div style={{ fontSize: '12px', marginBottom: '4px', color: 'var(--c-text-muted)' }}>Peak VRAM Usage</div>
          <div className="mono" style={{ fontSize: '14px', color: 'var(--c-text-primary)' }}>
            {metrics.vram_usage}
          </div>
          {/* Visual Bar */}
          <div style={{ width: '100%', height: '6px', marginTop: '8px', borderRadius: '3px', overflow: 'hidden', backgroundColor: 'var(--c-input-bg)' }}>
            <div style={{ height: '100%', width: '45%', backgroundColor: 'var(--c-gold)' }}></div>
          </div>
        </div>

        {/* Cost Est */}
        <div className="metric-group" style={{ borderTop: '1px solid var(--c-card-border)', paddingTop: '12px' }}>
          <div style={{ fontSize: '12px', marginBottom: '4px', color: 'var(--c-text-muted)' }}>Est. Power Cost</div>
          <div className="mono" style={{ fontSize: '14px', color: 'var(--c-warning)' }}>
            £{estCost}
          </div>
        </div>
      </div>
    </div>
  );
};
