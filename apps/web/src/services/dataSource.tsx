import { createContext, useContext, useState, type ReactNode } from 'react';
import type { DataSourceMode } from '../types/domain';

interface DataSourceContextValue {
  mode: DataSourceMode;
  setMode: (mode: DataSourceMode) => void;
}

const DataSourceContext = createContext<DataSourceContextValue>({
  mode: 'simulation',
  setMode: () => {},
});

export function DataSourceProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<DataSourceMode>('simulation');
  return (
    <DataSourceContext.Provider value={{ mode, setMode }}>
      {children}
    </DataSourceContext.Provider>
  );
}

export function useDataSource(): DataSourceContextValue {
  return useContext(DataSourceContext);
}
