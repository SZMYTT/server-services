// Standalone React Component

// Example data structure coming from orchestrator/websocket
interface TaskNode {
  id: string;
  title: string;
  status: 'pending' | 'running' | 'done' | 'error';
  children?: TaskNode[];
}

interface NodeMapProps {
  rootTask: TaskNode;
}

const NodeMap = ({ rootTask }) => {
  // Recursive rendering function for the node tree
  const renderNode = (node, depth = 0) => {
    // Status colors
    const statusColors = {
      pending: { bg: 'var(--c-input-bg)', border: 'var(--c-card-border)', text: 'var(--c-text-muted)' },
      running: { bg: 'var(--c-card)', border: 'var(--c-info)', text: 'var(--c-text-primary)' },
      done: { bg: 'var(--c-card)', border: 'var(--c-success)', text: 'var(--c-text-primary)' },
      error: { bg: 'var(--c-card)', border: 'var(--c-error)', text: 'var(--c-text-primary)' },
    };
    
    // Status indicators
    const statusDots = {
      pending: <div style={{width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--c-card-border)'}}></div>,
      running: <div className="rdot" style={{width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--c-info)', animation: 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite'}}></div>,
      done: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--c-success)" strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>,
      error: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--c-error)" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>,
    };

    const colors = statusColors[node.status] || statusColors.pending;

    return (
      <div key={node.id} style={{ display: 'flex', flexDirection: 'column', marginLeft: depth > 0 ? '24px' : '0' }}>
        {/* Connection line if not root */}
        {depth > 0 && (
          <div style={{ width: '1px', height: '16px', marginLeft: '16px', marginBottom: '4px', marginTop: '4px', opacity: 0.5, borderLeft: '1px dashed var(--c-card-border)' }}></div>
        )}
        
        {/* Node Card */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '12px', borderRadius: '4px', backgroundColor: colors.bg, border: `1px solid ${colors.border}`, color: colors.text, boxShadow: 'var(--shadow-card)', transition: 'transform 0.3s ease' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '16px', height: '16px' }}>{statusDots[node.status]}</div>
          <div style={{ fontWeight: 500, fontSize: '14px', flex: 1 }}>
            {node.title}
          </div>
          
          {/* Badge */}
          <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', padding: '2px 8px', borderRadius: '9999px', fontWeight: 'bold', backgroundColor: 'var(--c-canvas)', color: colors.text, border: `1px solid ${colors.border}` }}>
            {node.status}
          </div>
        </div>

        {/* Render children recursively */}
        {node.children && node.children.length > 0 && (
          <div style={{ marginTop: '4px', borderLeft: '2px solid var(--c-card-border)', marginLeft: '16px', paddingLeft: '8px' }}>
            {node.children.map(child => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="node-map-container" style={{ padding: '24px', borderRadius: '12px', overflow: 'auto', maxHeight: '600px', width: '100%', backgroundColor: 'var(--c-card)', border: '1px solid var(--c-card-border)', boxShadow: 'var(--shadow-card)' }}>
      <h2 style={{ fontSize: '18px', fontWeight: 'bold', marginBottom: '24px', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--c-text-primary)' }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--c-gold)" strokeWidth="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
        Mapmaker Execution Graph
      </h2>
      <div className="tree-wrapper">
        {renderNode(rootTask)}
      </div>
    </div>
  );
};
