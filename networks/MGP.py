import tensorflow as tf
import numpy as np
from .utils import * 

def SEARD(x1,x2,ARD_lambda,ARD_var0,M1,M2,D):
    """
    SE ARD(Squared Exponential Automatic Relevance Determination) Kernel
    k(x1,x2) = ARD_var0*exp(-0.5*\Sum_{d=1}^{D}(ARD_lambda[d]*(x1-x2)^2))
    x1 : (M1,D)
    x2 : (M2,D)
    """
    X1 = tf.reshape(tf.tile(x1,(1,M2)),shape=(M1*M2,D)) # (M1*M2,D)
    X2 = tf.tile(x2, (M1,1)) # (M1*M2,D)
    X1X2 = X1-X2 # (M1*M2,D)

    r = tf.reduce_sum(ARD_lambda*X1X2**2, axis=-1)
    kxx_vec = ARD_var0*tf.exp(-0.5*r)
    return tf.reshape(kxx_vec,shape=(M1,M2)) # (M1,M2) tensor
    
def MGPpredict(mX,mZ,m,S,Kzz,Kzz_inv,Kzx,Kxx,N,M,D_OUT):
    """
    Compute q(f|m,S;X,Z)
    mX:= mean(X) : (N,D_OUT) / mZ:=mean(Z) : (M,D_OUT)
    m : (M,D_OUT) / S : (M,M)
    Kzz : (M,M) /  Kzz_inv : (M,M) / Kzx : (M,N) / Kxx : scalar
    """
    alpha_temp = tf.transpose(Kzz_inv@Kzx) # (N,M)
    f_mu = mX + alpha_temp@(m-mZ) # (N,D_OUT)
    
    alpha = tf.expand_dims(alpha_temp,axis=2) # (N,M,1)
    alphaT = tf.transpose(alpha, perm=(0,2,1)) # (N,1,M)
    temp = expand_tile(Kzz-S,N) # (N,M,M)
    f_var = Kxx - tf.squeeze(alphaT@temp@alpha,axis=2) # (N,1)
    return f_mu, f_var

class MGP(object):
    def __init__(self, D_IN, D_OUT, M=50):
        """
        Initialize GP layer 
        D_IN    : dimension of input
        D_OUT   : dimension of output
        M       : the number of inducing points
        """
        self.sig_offset = 1e-2
        self.process_sig_offset = 1e-3
        self.lambda_offset = 1e-1
        self.D_IN = D_IN
        self.D_OUT = D_OUT
        self.M = M
        
        # Define mean function
        self._init_mean_function(D_IN, D_OUT)
        
        # Kernel Parameters (SE ARE kernel)
        self.ARD_loglambda = tf.Variable(np.zeros([1,D_IN]), dtype=tf.float32) # (1,Din)
        self.ARD_lambda = tf.exp(self.ARD_loglambda) + self.lambda_offset
        self.ARD_logsig0 = tf.Variable(0.0, dtype=tf.float32)
        self.ARD_var0 = (tf.exp(self.ARD_logsig0) + self.sig_offset)**2
            
        # Inducing Points (Z -> U)
        self.Z = tf.Variable(np.random.uniform(-3,3,[M,D_IN]), dtype=tf.float32) # (M,Din)
        self.GPmean = self.mean(self.Z)
        self.U_mean = tf.Variable(np.zeros([M,D_OUT]), dtype=tf.float32) # np.random.uniform(-2,2,[M,D_OUT])  # (M,Dout)
        self.U_logL_diag = tf.Variable(np.zeros([M]), dtype=tf.float32)  # (M,)
        self.U_L_diag = tf.exp(self.U_logL_diag)
        self.U_L_nondiag = tf.Variable(np.zeros([int(M*(M-1)/2)]), dtype=tf.float32)  # (M(M-1)/2,)    
        self.U_L = tf.matrix_set_diag(vec_to_tri(self.U_L_nondiag, M), self.U_L_diag) # (M,M)
        self.U_cov = (self.sig_offset**2)*tf.eye(M) + self.U_L@tf.transpose(self.U_L) # (M,M)
                
        # Covariance among inducing points
        self.Kzz = SEARD(self.Z,self.Z,self.ARD_lambda,self.ARD_var0,M,M,D_IN) + (self.sig_offset**2)*tf.eye(M) # (M,M)
        self.Kzz_L = tf.cholesky(self.Kzz)
        self.Kzz_L_inv = tf.matrix_triangular_solve(self.Kzz_L,tf.eye(M),lower=True) # tf.matrix_inverse(self.Kzz_L)
        self.Kzz_inv = tf.transpose(self.Kzz_L_inv)@self.Kzz_L_inv
        
        # Covariance among output
        if self.D_OUT == 1:
            self.Sig_logL = tf.Variable(np.zeros([D_OUT,D_OUT]), dtype=tf.float32)  # (Dout,)
            self.Sig_L = tf.exp(self.Sig_logL)
            self.Sig = (self.sig_offset**2)*tf.eye(D_OUT) + self.Sig_L**2
            self.Sig_inv = 1/self.Sig
        else:
            self.Sig_logL_diag = tf.Variable(np.zeros([D_OUT]), dtype=tf.float32)  # (Dout,)
            self.Sig_L_diag = tf.exp(self.Sig_logL_diag)
            self.Sig_L_nondiag = tf.Variable(np.zeros([int(D_OUT*(D_OUT-1)/2)]), dtype=tf.float32)  # (Dout(Dout-1)/2,)    
            self.Sig_L = tf.matrix_set_diag(vec_to_tri(self.Sig_L_nondiag, D_OUT), self.Sig_L_diag) # (Dout,Dout)
            self.Sig = (self.sig_offset**2)*tf.eye(D_OUT) + self.Sig_L@tf.transpose(self.Sig_L) # (Dout,Dout)
            
            self.Sig = self.Sig
            
            self.Sig_L_inv = tf.matrix_triangular_solve(self.Sig_L,tf.eye(D_OUT),lower=True) # tf.matrix_inverse(self.Sig_L)
            self.Sig_inv = tf.transpose(self.Sig_L_inv)@self.Sig_L_inv
            
        # Processing Noise
        self.logbeta = tf.Variable(-0.5*np.ones([1,D_OUT]), dtype=tf.float32) # set as -1.0
        self.beta = tf.exp(self.logbeta) + self.process_sig_offset
    
    def _init_mean_function(self,D_IN, D_OUT):
        # self.H = W = tf.Variable(np.zeros([D_IN,D_OUT]), dtype=tf.float32)
        # self.B = tf.Variable(np.zeros([1,D_OUT]), dtype=tf.float32) # (1,Dout)

        H0 = np.zeros([D_IN,D_OUT])     
        self.H = W = tf.constant(H0, dtype=tf.float32)
        self.B = tf.constant(np.zeros([1,D_OUT]), dtype=tf.float32) # (1,Dout)
      
    def mean(self, X):
        # X : (N,D_in)
        return X@self.H + self.B 
    
    def predict(self, x):
        # X : (N,D_IN)
        N = tf.shape(x)[0]
        Kzx = SEARD(self.Z, x, self.ARD_lambda, self.ARD_var0, self.M, N, self.D_IN)
        f_mu, f_var= MGPpredict(self.mean(x), self.GPmean
                                , self.U_mean, self.U_cov
                                , self.Kzz, self.Kzz_inv, Kzx, self.ARD_var0
                                , N, self.M, self.D_OUT)
        
        epsilon = tf.random_normal((N,self.D_OUT,1)) # (N,D_OUT,1)    
        Lexpand = tf.tile(tf.expand_dims(self.Sig_L,axis=0),(N,1,1)) # (N,D_OUT,D_OUT)
        f = f_mu + tf.sqrt(f_var)*tf.squeeze(Lexpand@epsilon,axis=2) # (N,D_OUT)
        
        return f + tf.random_normal((N,self.D_OUT))*self.beta # (N,D_OUT)
    
    def KL(self):
        """
        Compute KL(q(U),p(U)), where q(U)~MN(mu0,cov0,Sig), p(U)~MN(mu1,cov1,Sig)
        mu0, mu1   : (M,D)
        cov0, cov1 : (M,M)
        Sig        : (D,D)
        """
            
        term1 = self.D_OUT*tf.reduce_sum(self.Kzz_inv*self.U_cov)
        mu10 = self.GPmean - self.U_mean # (M,D)
        term2_1 = mu10@self.Sig_inv # (M,D)
        term2_2 = self.Kzz_inv@mu10 # (M,D)
        term2 = tf.reduce_sum(term2_1*term2_2)
        term3_1 = -logdet(self.U_L)
        term3_2 = -logdet(self.Kzz_L_inv)
        
        return 0.5*(term1 + term2 + term3_1*self.D_OUT + term3_2*self.D_OUT - self.M*self.D_OUT)