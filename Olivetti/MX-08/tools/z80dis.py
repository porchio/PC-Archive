#!/usr/bin/env python3
"""Minimal but complete Z80 disassembler for the Olivetti MX-08 dump."""
import sys

r = ['B','C','D','E','H','L','(HL)','A']
rp = ['BC','DE','HL','SP']
rp2 = ['BC','DE','HL','AF']
cc = ['NZ','Z','NC','C','PO','PE','P','M']
alu = ['ADD A,','ADC A,','SUB ','SBC A,','AND ','XOR ','OR ','CP ']
rot = ['RLC','RRC','RL','RR','SLA','SRA','SLL','SRL']

def u8(d,p): return d[p]
def s8(v): return v-256 if v>=128 else v
def u16(d,p): return d[p]|(d[p+1]<<8)

def disasm(d, pc):
    """Return (length, text). pc is offset = address for ROM based at 0."""
    start=pc
    op=d[pc]; pc+=1
    pre=None
    # handle DD/FD
    idx=None
    if op in (0xDD,0xFD):
        idx='IX' if op==0xDD else 'IY'
        op=d[pc]; pc+=1
    def ireg(name):
        # replace HL/(HL) with index
        return name
    if op==0xCB:
        if idx:
            disp=s8(d[pc]); pc+=1
        op2=d[pc]; pc+=1
        x=op2>>6; y=(op2>>3)&7; z=op2&7
        if idx:
            tgt=f'({idx}{disp:+d})'
            if x==0: txt=f'{rot[y]} {tgt}'
            elif x==1: txt=f'BIT {y},{tgt}'
            elif x==2: txt=f'RES {y},{tgt}'
            else: txt=f'SET {y},{tgt}'
        else:
            tgt=r[z]
            if x==0: txt=f'{rot[y]} {tgt}'
            elif x==1: txt=f'BIT {y},{tgt}'
            elif x==2: txt=f'RES {y},{tgt}'
            else: txt=f'SET {y},{tgt}'
        return pc-start, txt
    if op==0xED:
        op2=d[pc]; pc+=1
        x=op2>>6; y=(op2>>3)&7; z=op2&7; p=y>>1; q=y&1
        ed={0x44:'NEG',0x45:'RETN',0x4D:'RETI',0x46:'IM 0',0x56:'IM 1',0x5E:'IM 2',
            0x47:'LD I,A',0x4F:'LD R,A',0x57:'LD A,I',0x5F:'LD A,R',
            0x67:'RRD',0x6F:'RLD',0xA0:'LDI',0xA1:'CPI',0xA2:'INI',0xA3:'OUTI',
            0xA8:'LDD',0xA9:'CPD',0xAA:'IND',0xAB:'OUTD',0xB0:'LDIR',0xB1:'CPIR',
            0xB2:'INIR',0xB3:'OTIR',0xB8:'LDDR',0xB9:'CPDR',0xBA:'INDR',0xBB:'OTDR'}
        if op2 in ed: return pc-start, ed[op2]
        if x==1:
            if z==0: return pc-start, f'IN {r[y]},(C)' if y!=6 else 'IN (C)'
            if z==1: return pc-start, f'OUT (C),{r[y]}' if y!=6 else 'OUT (C),0'
            if z==2:
                return pc-start, (f'SBC HL,{rp[p]}' if q==0 else f'ADC HL,{rp[p]}')
            if z==3:
                nn=u16(d,pc); pc+=2
                if q==0: return pc-start, f'LD (${nn:04X}),{rp[p]}'
                else: return pc-start, f'LD {rp[p]},(${nn:04X})'
        return pc-start, f'DB $ED,${op2:02X}'
    # main page
    x=op>>6; y=(op>>3)&7; z=op&7; p=y>>1; q=y&1
    H='HL' if not idx else idx
    Hm='(HL)' if not idx else None
    def mem_idx():
        nonlocal pc
        disp=s8(d[pc]); pc+=1
        return f'({idx}{disp:+d})'
    if x==0:
        if z==0:
            if y==0: return pc-start,'NOP'
            if y==1: return pc-start,"EX AF,AF'"
            if y==2:
                e=s8(d[pc]); pc+=1; return pc-start, f'DJNZ ${start+2+e:04X}'
            if y==3:
                e=s8(d[pc]); pc+=1; return pc-start, f'JR ${start+2+e:04X}'
            e=s8(d[pc]); pc+=1; return pc-start, f'JR {cc[y-4]},${start+2+e:04X}'
        if z==1:
            if q==0:
                nn=u16(d,pc); pc+=2
                reg=rp[p] if p!=2 else H
                return pc-start, f'LD {reg},${nn:04X}'
            else:
                reg=rp[p] if p!=2 else H
                return pc-start, f'ADD {H},{reg}'
        if z==2:
            if q==0:
                if p==0: return pc-start,'LD (BC),A'
                if p==1: return pc-start,'LD (DE),A'
                if p==2:
                    nn=u16(d,pc); pc+=2; return pc-start, f'LD (${nn:04X}),{H}'
                nn=u16(d,pc); pc+=2; return pc-start, f'LD (${nn:04X}),A'
            else:
                if p==0: return pc-start,'LD A,(BC)'
                if p==1: return pc-start,'LD A,(DE)'
                if p==2:
                    nn=u16(d,pc); pc+=2; return pc-start, f'LD {H},(${nn:04X})'
                nn=u16(d,pc); pc+=2; return pc-start, f'LD A,(${nn:04X})'
        if z==3:
            reg=rp[p] if p!=2 else H
            return pc-start, (f'INC {reg}' if q==0 else f'DEC {reg}')
        if z==4 or z==5:
            op_='INC' if z==4 else 'DEC'
            if y==6 and idx:
                tgt=mem_idx()
            else:
                tgt=r[y] if not (idx and y in(4,5)) else (idx+('h' if y==4 else 'l'))
            return pc-start, f'{op_} {tgt}'
        if z==6:
            if y==6 and idx:
                tgt=mem_idx()
            else:
                tgt=r[y]
            n=d[pc]; pc+=1
            return pc-start, f'LD {tgt},${n:02X}'
        if z==7:
            misc=['RLCA','RRCA','RLA','RRA','DAA','CPL','SCF','CCF']
            return pc-start, misc[y]
    if x==1:
        if z==6 and y==6: return pc-start,'HALT'
        # LD r,r'
        if idx and (y==6 or z==6):
            if y==6:
                dst=mem_idx(); src=r[z]
            else:
                src=mem_idx(); dst=r[y]
            return pc-start, f'LD {dst},{src}'
        return pc-start, f'LD {r[y]},{r[z]}'
    if x==2:
        if z==6 and idx:
            tgt=mem_idx()
        else:
            tgt=r[z]
        return pc-start, f'{alu[y]}{tgt}'
    if x==3:
        if z==0: return pc-start, f'RET {cc[y]}'
        if z==1:
            if q==0:
                return pc-start, f'POP {rp2[p] if p!=2 else H}'
            sp=['RET','EXX','JP (HL)','LD SP,HL'][p]
            if p==2 and idx: sp=f'JP ({idx})'
            if p==3 and idx: sp=f'LD SP,{idx}'
            return pc-start, sp
        if z==2:
            nn=u16(d,pc); pc+=2; return pc-start, f'JP {cc[y]},${nn:04X}'
        if z==3:
            if y==0:
                nn=u16(d,pc); pc+=2; return pc-start, f'JP ${nn:04X}'
            if y==1: return pc-start,'(CB prefix)'
            if y==2:
                n=d[pc]; pc+=1; return pc-start, f'OUT (${n:02X}),A'
            if y==3:
                n=d[pc]; pc+=1; return pc-start, f'IN A,(${n:02X})'
            if y==4: return pc-start, f'EX (SP),{H}'
            if y==5: return pc-start,'EX DE,HL'
            if y==6: return pc-start,'DI'
            if y==7: return pc-start,'EI'
        if z==4:
            nn=u16(d,pc); pc+=2; return pc-start, f'CALL {cc[y]},${nn:04X}'
        if z==5:
            if q==0:
                return pc-start, f'PUSH {rp2[p] if p!=2 else H}'
            if p==0:
                nn=u16(d,pc); pc+=2; return pc-start, f'CALL ${nn:04X}'
            return pc-start, '(DD/ED/FD)'
        if z==6:
            n=d[pc]; pc+=1; return pc-start, f'{alu[y]}${n:02X}'
        if z==7:
            return pc-start, f'RST ${y*8:02X}'
    return pc-start, f'DB ${op:02X}'

if __name__=='__main__':
    d=open(sys.argv[1],'rb').read()
    start=int(sys.argv[2],16) if len(sys.argv)>2 else 0
    end=int(sys.argv[3],16) if len(sys.argv)>3 else len(d)
    pc=start
    while pc<end:
        try:
            ln,txt=disasm(d,pc)
        except Exception as e:
            ln,txt=1,f'DB ${d[pc]:02X} ; err {e}'
        b=' '.join(f'{x:02X}' for x in d[pc:pc+ln])
        print(f'{pc:04X}  {b:<14} {txt}')
        pc+=ln
