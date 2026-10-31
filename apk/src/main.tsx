import { render } from 'preact';
import './styles/global.css';
import { App } from './app';

const el = document.getElementById('app');
if (el) render(<App />, el);
