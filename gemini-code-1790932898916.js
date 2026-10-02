import React, { useState, useEffect } from 'react';
import { Plus, Trash2, FolderPlus, Layers, Search, ShieldCheck, Stock } from 'lucide-react';

export default function GroupManager() {
  const [groups, setGroups] = useState([]);
  const [selectedGroupId, setSelectedGroupId] = useState(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupType, setNewGroupType] = useState('normal');
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    fetch('/api/groups')
      .then((res) => res.json())
      .then((data) => {
        setGroups(data);
        if (data.length > 0) setSelectedGroupId(data[0].id);
      })
      .catch(() => {
        // Fallback demo data
        const mock = [
          { id: 1, name: 'ALL-ONE ETFs (Domestic)', group_type: 'normal', constituents: ['ABSLPSE', 'ALPHA', 'AONETOTAL', 'AUTOBEES', 'BANKBEES', 'BFSI', 'CHEMICAL', 'CONSUMBEES', 'CPSEETF', 'JUNIORBEES', 'LOWVOLIETF'] },
          { id: 2, name: 'Defensive Cash Proxies', group_type: 'defensive', constituents: ['GILT5YBEES', 'LIQUIDBEES', 'GOLDBEES'] }
        ];
        setGroups(mock);
        setSelectedGroupId(1);
      });
  }, []);

  const selectedGroup = groups.find((g) => g.id === selectedGroupId);

  const handleAddSymbol = () => {
    if (!newSymbol.trim() || !selectedGroup) return;
    const formatted = newSymbol.trim().toUpperCase();
    if (selectedGroup.constituents.includes(formatted)) return alert('Symbol already exists in group');

    const updatedConstituents = [...selectedGroup.constituents, formatted];
    
    fetch(`/api/groups/${selectedGroup.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...selectedGroup, constituents: updatedConstituents })
    }).then(() => {
      setGroups(groups.map((g) => (g.id === selectedGroup.id ? { ...g, constituents: updatedConstituents } : g)));
      setNewSymbol('');
    });
  };

  const handleDeleteSymbol = (symbol) => {
    const updatedConstituents = selectedGroup.constituents.filter((s) => s !== symbol);
    fetch(`/api/groups/${selectedGroup.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...selectedGroup, constituents: updatedConstituents })
    }).then(() => {
      setGroups(groups.map((g) => (g.id === selectedGroup.id ? { ...g, constituents: updatedConstituents } : g)));
    });
  };

  const handleCreateGroup = () => {
    if (!newGroupName.trim()) return;
    fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newGroupName.trim(), group_type: newGroupType, constituents: [] })
    })
      .then((res) => res.json())
      .then((data) => {
        const created = { id: data.id || Date.now(), name: newGroupName.trim(), group_type: newGroupType, constituents: [] };
        setGroups([...groups, created]);
        setSelectedGroupId(created.id);
        setNewGroupName('');
      });
  };

  const filteredConstituents = selectedGroup?.constituents.filter((s) =>
    s.toLowerCase().includes(searchTerm.toLowerCase())
  ) || [];

  return (
    <div className="space-y-6">
      {/* Title & Quick Create Header */}
      <div className="bg-slate-800/80 p-5 rounded-2xl border border-slate-700/80 shadow-lg flex flex-col md:flex-row justify-between md:items-center gap-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center space-x-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <span>Universe Group Manager</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">Organize trading tickers into Normal momentum pools or Defensive cash proxies.</p>
        </div>

        <div className="flex items-center space-x-2 bg-slate-900/90 p-2 rounded-xl border border-slate-700">
          <input
            type="text"
            placeholder="New Group Name..."
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            className="bg-slate-800 border border-slate-700 text-white px-3 py-1.5 rounded-lg text-sm focus:outline-none focus:border-blue-500"
          />
          <select
            value={newGroupType}
            onChange={(e) => setNewGroupType(e.target.value)}
            className="bg-slate-800 border border-slate-700 text-slate-300 px-2 py-1.5 rounded-lg text-sm focus:outline-none"
          >
            <option value="normal">Normal</option>
            <option value="defensive">Defensive</option>
          </select>
          <button
            onClick={handleCreateGroup}
            className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded-lg text-sm font-bold flex items-center space-x-1 transition shadow"
          >
            <FolderPlus className="w-4 h-4" />
            <span>Create</span>
          </button>
        </div>
      </div>

      {/* Dual Pane Layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Left Pane: Groups List */}
        <div className="bg-slate-800/50 rounded-2xl border border-slate-700/80 p-4 space-y-3">
          <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider px-1">Select Universe Group</h2>
          <div className="space-y-2">
            {groups.map((group) => {
              const isSelected = selectedGroupId === group.id;
              return (
                <div
                  key={group.id}
                  onClick={() => setSelectedGroupId(group.id)}
                  className={`p-3.5 rounded-xl cursor-pointer border transition-all flex justify-between items-center ${
                    isSelected
                      ? 'bg-blue-600/20 border-blue-500 text-white shadow-md'
                      : 'bg-slate-900/40 border-slate-800 text-slate-300 hover:bg-slate-800/80 hover:border-slate-700'
                  }`}
                >
                  <div className="font-semibold text-sm">{group.name}</div>
                  <span
                    className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider border ${
                      group.group_type === 'defensive'
                        ? 'bg-amber-950/60 border-amber-700/80 text-amber-400'
                        : 'bg-blue-950/60 border-blue-700/80 text-blue-400'
                    }`}
                  >
                    {group.group_type}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Pane: Constituents Table */}
        <div className="md:col-span-2 bg-slate-800/50 rounded-2xl border border-slate-700/80 p-5 space-y-4">
          <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-3 border-b border-slate-700/60 pb-4">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center space-x-2">
                <span>{selectedGroup?.name}</span>
                <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-md font-mono">
                  {selectedGroup?.constituents.length || 0} Symbols
                </span>
              </h2>
            </div>

            {/* Add Symbol Input */}
            <div className="flex items-center space-x-2">
              <input
                type="text"
                placeholder="Add Ticker (e.g. NIFTYBEES)"
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value)}
                className="bg-slate-900 border border-slate-700 text-white px-3 py-1.5 rounded-lg text-sm focus:outline-none focus:border-blue-500 uppercase"
              />
              <button
                onClick={handleAddSymbol}
                className="bg-emerald-600 hover:bg-emerald-500 text-white px-3.5 py-1.5 rounded-lg text-sm font-bold transition shadow"
              >
                Add Symbol
              </button>
            </div>
          </div>

          {/* Search Filter Bar */}
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search constituents in group..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl pl-9 pr-3 py-2 text-xs text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Table */}
          <div className="overflow-x-auto rounded-xl border border-slate-700/60 bg-slate-900/60">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 text-slate-400 uppercase text-[11px] font-semibold tracking-wider">
                <tr>
                  <th className="py-3 px-4">#</th>
                  <th className="py-3 px-4">Symbol Ticker</th>
                  <th className="py-3 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 text-slate-200">
                {filteredConstituents.map((symbol, idx) => (
                  <tr key={symbol} className="hover:bg-slate-800/40 transition">
                    <td className="py-3 px-4 font-mono text-slate-500 text-xs">{idx + 1}</td>
                    <td className="py-3 px-4 font-bold text-white tracking-wide">{symbol}</td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => handleDeleteSymbol(symbol)}
                        className="text-slate-500 hover:text-red-400 p-1.5 rounded-lg hover:bg-red-950/40 transition"
                        title="Remove Symbol"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}

                {filteredConstituents.length === 0 && (
                  <tr>
                    <td colSpan="3" className="text-center py-8 text-slate-500 text-sm">
                      No constituents found in this group.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}