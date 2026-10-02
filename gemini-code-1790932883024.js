import React, { useState } from 'react';
import Navbar from './components/Navbar';
import Dashboard from './components/Dashboard';
import GroupManager from './components/GroupManager';
import StrategyBuilder from './components/StrategyBuilder';
import RebalanceScanner from './components/RebalanceScanner';
import PositionsTracker from './components/PositionsTracker';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedStrategyId, setSelectedStrategyId] = useState(1);

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col font-sans">
      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        {activeTab === 'dashboard' && (
          <Dashboard 
            setActiveTab={setActiveTab} 
            setSelectedStrategyId={setSelectedStrategyId} 
          />
        )}

        {activeTab === 'group-manager' && (
          <GroupManager />
        )}

        {activeTab === 'strategy-builder' && (
          <StrategyBuilder 
            setActiveTab={setActiveTab} 
          />
        )}

        {activeTab === 'scanner' && (
          <RebalanceScanner 
            strategyId={selectedStrategyId} 
            setActiveTab={setActiveTab} 
          />
        )}

        {activeTab === 'positions' && (
          <PositionsTracker 
            strategyId={selectedStrategyId} 
            setActiveTab={setActiveTab} 
          />
        )}
      </main>
    </div>
  );
}